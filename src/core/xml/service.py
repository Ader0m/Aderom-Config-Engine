# -*- coding: utf-8 -*-
"""XmlResolveService — кэш и оркестрация XML-резолва.

Оборачивает :class:`XmlResolver` общим :class:`ResolvedCache`:

- один экземпляр ``XmlResolver`` на каталог configs/DXML-режим
  (список файлов и подпись скриптов считаются один раз на сессию);
- результат ``XmlResolveOutput`` кладётся в память и на диск;
- одновременный резолв одного файла дедуплицируется.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from core.resolver.base import miss_reason
from core.tools.cache import ResolvedCache, path_key
from core.tools.fs import canonical_config_dir
from core.xml.resolver import XmlResolver

logger = logging.getLogger(__name__)

# Порог диагностики медленных операций XML-резолва.
_SLOW_XML_SECONDS = 0.25


class XmlResolveService:
    """Кэширующий сервис XML-резолва."""

    def __init__(self, resolved_cache=None, scripts_dir=None):
        self._resolved_cache = resolved_cache or ResolvedCache()
        self._scripts_dir = scripts_dir
        self._lock = threading.RLock()
        self._resolvers = {}
        self._inflight = {}
        self._last_keys = {}

    # -------------------------------------------------- резолверы
    def effective_config_dir(self, config_dir):
        """Канонический каталог configs (game root → gamedata/configs)."""
        return canonical_config_dir(config_dir)

    def _resolver(self, config_dir, dxml=True) -> XmlResolver:
        config_dir = canonical_config_dir(config_dir)
        key = (
            path_key(config_dir) if config_dir is not None else "",
            bool(dxml),
        )
        with self._lock:
            resolver = self._resolvers.get(key)
            if resolver is None:
                resolver = XmlResolver(
                    config_dir=config_dir,
                    scripts_dir=self._scripts_dir,
                    dxml=dxml,
                )
                self._resolvers[key] = resolver
            return resolver

    def refresh(self):
        """Сбросить кэши каталогов у всех резолверов."""
        with self._lock:
            resolvers = list(self._resolvers.values())
        for resolver in resolvers:
            resolver.refresh()

    # -------------------------------------------------- кэш
    def cache_stats(self):
        return self._resolved_cache.stats()

    def clear(self):
        self._resolved_cache.clear()

    def cache_key(self, path, config_dir, dxml=True, overlay=None):
        resolver = self._resolver(config_dir, dxml)
        return resolver.cache_key(path, dxml=dxml, overlay=overlay)

    def has_resolved(self, path, config_dir, dxml=True, overlay=None):
        key = self.cache_key(path, config_dir, dxml, overlay=overlay)
        return self._resolved_cache.has(key)

    def has_resolved_by_key(self, key):
        return self._resolved_cache.has(key)

    def store_output(self, path, config_dir, value, dxml=True, key=None,
                     overlay=None):
        if key is None:
            key = self.cache_key(
                path,
                config_dir,
                dxml,
                overlay=overlay,
            )
        self._resolved_cache.set(key, value)
        return key

    def store_output_by_key(self, key, value):
        self._resolved_cache.set(key, value)

    # -------------------------------------------------- резолв
    def resolve_output(self, path, config_dir, log=None, dxml=True,
                       key=None, overlay=None):
        resolver = self._resolver(config_dir, dxml)
        if key is None:
            started = time.perf_counter()
            key = resolver.cache_key(path, dxml=dxml, overlay=overlay)
            key_elapsed = time.perf_counter() - started
            if key_elapsed >= _SLOW_XML_SECONDS:
                logger.warning(
                    "xml key: %.2f s для %s",
                    key_elapsed,
                    Path(path).name,
                )
        name = Path(path).name
        previous = None
        if isinstance(key, tuple) and len(key) > 3:
            with self._lock:
                previous = self._last_keys.get(key[2])
                self._last_keys[key[2]] = key

        started = time.perf_counter()
        cached = self._resolved_cache.get(key)
        load_elapsed = time.perf_counter() - started
        if load_elapsed >= _SLOW_XML_SECONDS:
            logger.warning(
                "xml cache: %.2f s на чтение %s",
                load_elapsed,
                name,
            )
        if cached is not None:
            if log:
                log("xml cache: hit %s" % name)
            return cached

        with self._lock:
            key_lock = self._inflight.get(key)
            if key_lock is None:
                key_lock = threading.Lock()
                self._inflight[key] = key_lock

        with key_lock:
            try:
                cached = self._resolved_cache.get(key, count_miss=False)
                if cached is not None:
                    if log:
                        log("xml cache: hit %s" % name)
                    return cached

                value = resolver.resolve_output(
                    path,
                    dxml=dxml,
                    overlay=overlay,
                )
                self._resolved_cache.set(key, value)
                if log:
                    log(
                        "xml cache: miss %s (%s)"
                        % (
                            name,
                            miss_reason(
                                previous,
                                key,
                                (
                                    "version",
                                    "config",
                                    "path",
                                    "include/deps",
                                    "overlay",
                                    "scripts",
                                    "dxml",
                                ),
                            ),
                        )
                    )
                return value
            finally:
                with self._lock:
                    if self._inflight.get(key) is key_lock:
                        self._inflight.pop(key, None)

    def resolve_with_virtual_xml_patch(
        self,
        source_path,
        root_path,
        plan,
        log=None,
    ):
        """Собрать XML с виртуальным DXML-патчем из плана.

        Возвращает :class:`XmlResolveOutput`. Патч не пишется на диск:
        колбэк регистрируется только в локальном экземпляре процессора.
        """
        from core.xml.dxml import DxmlProcessor
        from core.xml.resolver import XmlResolveOutput, XmlResolver
        from core.xml.changes import apply_changes_to_xml_obj

        config_dir = getattr(plan, "config_dir", None)
        if config_dir is None:
            config_dir = self.effective_config_dir(source_path)
        if config_dir is None:
            raise ValueError("Не определён каталог configs для XML-проверки")

        resolver = XmlResolver(
            config_dir=config_dir,
            scripts_dir=self._scripts_dir,
            dxml=True,
        )
        virtual_name = resolver.virtual_name(root_path)
        assembled, origins = resolver.assemble_with_origins(virtual_name)

        processor = DxmlProcessor(
            include_loader=resolver.load_include_text,
            log=log or (lambda message: None),
            scripts_dir=resolver.scripts_dir,
            builtin_patches=bool(resolver._dxml_enabled),
        )

        document = getattr(getattr(plan, "diff", None), "before", None)
        if document is None:
            raise ValueError("В плане нет исходного XML-документа")

        def callback(xml_file_name, xml_obj, flags):
            apply_changes_to_xml_obj(
                xml_obj,
                document,
                plan.changes,
            )

        processor.register_callback(callback, name="ace_virtual")

        text, _xml_obj = processor.process_dom(
            virtual_name,
            assembled,
            origins,
        )
        return XmlResolveOutput(
            text=text,
            virtual_name=virtual_name,
            dxml_applied=True,
            notes=tuple(processor.unsupported_scripts),
            line_sources=(),
        )
