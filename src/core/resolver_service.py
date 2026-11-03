# src/core/resolver_service.py
# -*- coding: utf-8 -*-
"""
ResolverService — сервис ядра для оркестрации работы с внешним резолвером.

Отвечает за:
- поиск и подключение внешнего инструмента (Resolver4.py);
- выполнение прохода резолвера;
- сборку финального текста конфигурации;
- мутацию доменных объектов (forest.resolver_sections);
- логирование процесса.

НЕ отвечает за:
- парсинг LTX-файлов (это делает внешний резолвер);
- построение дерева (это делает TreeBuilder);
- генерацию патчей (это делает PatchPlanner).
"""

import logging
import threading
from pathlib import Path

from .fs import (
    build_search_bases,
    find_resolver_script,
    build_source_map,
    normalize_path,
)
from .resolver_adapter import LtxResolverAdapter
from .resolved_cache import (
    ResolvedCache,
    file_subtree_signature,
    resolver_signature,
)

logger = logging.getLogger(__name__)


class ResolverService:
    """Сервис ядра для оркестрации работы с внешним резолвером."""

    def __init__(self, cache, resolved_cache=None):
        """
        Args:
            cache: FileCacheSystem — общий кэш ядра.
            resolved_cache: ResolvedCache — кэш результатов резолва.
        """
        self._cache = cache
        self._resolved_cache = resolved_cache or ResolvedCache()
        self._adapter = None
        self._inflight_lock = threading.RLock()
        self._inflight = {}

    def invalidate_cache(self):
        """Сбросить кэш резолва (вызывается при «Чистом запуске»)."""
        self._resolved_cache.clear()

    def cache_stats(self):
        return self._resolved_cache.stats()

    # -------------------------------------------------- адаптер

    def _ensure_adapter(self, forest, config_dir):
        """Найти и создать адаптер, если ещё не создан."""
        if self._adapter is not None:
            return self._adapter

        bases = build_search_bases(forest.scan_dir, config_dir)
        script = find_resolver_script(bases)
        if script is None:
            logger.warning("Resolver4.py не найден в базах: %s", bases)
            return None

        self._adapter = LtxResolverAdapter(script, cache=self._cache)
        logger.info("Resolver4 подключён: %s", self._adapter.resolver_script)
        return self._adapter

    def get_adapter(self):
        """Вернуть текущий адаптер (или None)."""
        return self._adapter

    def _resolved_key(self, kind, root_path, forest, config_dir):
        """Ключ кэша: результат зависит от файла и его зависимостей.

        Подпись поддерева включает mtime+size самого файла, include-файлов
        и присоединённых mod_*.ltx, а также подпись Resolver4.py.
        """
        config = (
            str(normalize_path(config_dir))
            if config_dir is not None
            else ""
        )
        return (
            "resolve-v1",
            kind,
            str(normalize_path(root_path)),
            file_subtree_signature(forest, root_path),
            config,
            resolver_signature(
                getattr(self._adapter, "resolver_script", None)
            ),
        )

    def _cache_call(self, kind, root_path, forest, config_dir,
                    compute, log=None):
        """Кэш + дедупликация одновременного резолва одного файла.

        Пользователь может открыть файл, который уже резолвится фоновой
        предсборкой: второй поток дождётся результата, а не запустит
        повторный проход.
        """
        key = self._resolved_key(kind, root_path, forest, config_dir)

        cached = self._resolved_cache.get(key)
        if cached is not None:
            if log:
                log("resolver cache: hit %s" % Path(root_path).name)
            return cached

        with self._inflight_lock:
            key_lock = self._inflight.get(key)
            if key_lock is None:
                key_lock = threading.Lock()
                self._inflight[key] = key_lock

        with key_lock:
            cached = self._resolved_cache.get(key, count_miss=False)
            if cached is not None:
                if log:
                    log("resolver cache: hit %s" % Path(root_path).name)
                return cached

            value = compute()
            self._resolved_cache.set(key, value)
            return value

    # -------------------------------------------------- предсборка
    def source_maps(self, forest):
        """Карты источников и абсолютных путей для Resolver4."""
        sm = build_source_map(forest)

        # Resolver4 хранит в Item.filename либо stem базового имени
        # (корень и mod_*-файлы), либо stem ПОЛНОГО пути (файлы, загруженные
        # через #include). Обратная карта в абсолютные пути строится по обеим
        # формам ключа, иначе include-файлы теряются.
        abs_map = {}
        ltx_files = getattr(forest, "files", None)
        if ltx_files is None:
            ltx_files = [
                Path(p)
                for p in (getattr(forest, "rel_paths", {}) or {})
                if Path(p).suffix.lower() == ".ltx"
            ]
        for node in ltx_files:
            node = Path(node)
            low = str(node).lower()
            suffix_low = node.suffix.lower()
            full_stem = low[: -len(suffix_low)] if suffix_low else low
            for map_key in (node.stem.lower(), full_stem):
                abs_map.setdefault(map_key, node)

        return sm, abs_map

    def has_resolved(self, root_path, forest, config_dir):
        """Есть ли готовый resolved-кэш для файла."""
        key = self._resolved_key(
            "resolved",
            root_path,
            forest,
            config_dir,
        )
        return self._resolved_cache.has(key)

    def store_resolved(self, root_path, forest, config_dir, value):
        """Положить результат фоновой сборки в кэш."""
        key = self._resolved_key(
            "resolved",
            root_path,
            forest,
            config_dir,
        )
        self._resolved_cache.set(key, value)

    # -------------------------------------------------- проход резолвера

    def run_pass(self, forest, config_dir, log=None):
        """
        Выполнить проход резолвера по дереву.

        Это бывший метод фасада `resolver_pass`.
        Вся логика (поиск скрипта, вызов адаптера, мутация forest) теперь здесь.

        Args:
            forest: Построенное дерево файлов.
            config_dir: Каталог конфигов (для поиска скрипта).
            log: Опциональная функция логирования.

        Returns:
            int | None: Количество обработанных секций, или None если скрипт не найден.
        """
        adapter = self._ensure_adapter(forest, config_dir)
        if adapter is None:
            if log:
                log("Resolver4.py не найден — проход пропущен")
            return None

        if log:
            log("Resolver4: %s" % adapter.resolver_script)

        count = adapter.pass_only(forest.system_root)
        forest.resolver_sections = count  # Мутация доменного объекта — задача сервиса

        if log:
            log("Resolver4 pass: секций %s" % count)

        return count

    # -------------------------------------------------- сборка текста

    def assemble_text(self, root_path, forest, config_dir, log=None):
        """
        Собрать финальный текст конфигурации для файла.

        Это бывший метод фасада `assemble_text`.

        Args:
            root_path: Путь к корневому файлу.
            forest: Построенное дерево файлов.
            config_dir: Каталог конфигов.
            log: Опциональная функция логирования.

        Returns:
            str: Итоговый текст конфигурации.

        Raises:
            RuntimeError: Если резолвер не найден.
        """
        adapter = self._ensure_adapter(forest, config_dir)
        if adapter is None:
            raise RuntimeError("Resolver4.py не найден")

        def compute():
            sm = build_source_map(forest)
            return (adapter.assemble_text(root_path, sm), None)

        return self._cache_call(
            "text",
            root_path,
            forest,
            config_dir,
            compute,
            log=log,
        )[0]

    def assemble_resolved(self, root_path, forest, config_dir, log=None):
        adapter = self._ensure_adapter(forest, config_dir)
        if adapter is None:
            raise RuntimeError("Resolver4.py не найден")

        def compute():
            sm, abs_map = self.source_maps(forest)
            return adapter.assemble_resolved(root_path, sm, abs_map)

        return self._cache_call(
            "resolved",
            root_path,
            forest,
            config_dir,
            compute,
            log=log,
        )

    def assemble_to_file(self, root_path, forest, out_dir, config_dir=None):
        """Собрать текст (через кэш) и записать в out_dir."""
        if config_dir is None:
            config_dir = getattr(forest, "scan_dir", None)

        text = self.assemble_text(
            root_path,
            forest,
            config_dir,
        )

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        rp = Path(root_path)

        def safe(s):
            return "".join(
                c if c.isalnum() or c in "_-" else "_"
                for c in str(s)
            ).lower()

        out = out_dir / (
            "%s__%s__assembled.ltx"
            % (safe(rp.parent.name), safe(rp.stem))
        )
        out.write_text(text, encoding="utf-8")
        return out