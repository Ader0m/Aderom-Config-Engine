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

import inspect
import logging
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from core.resolver.attribution import build_source_map
from core.resolver.base import miss_reason
from core.resolver.ltx import LtxResolverAdapter
from core.tools.fs import (
    build_search_bases,
    find_resolver_script,
)
from core.tools.text import safe_filename_component
from core.tools.cache import (
    ResolvedCache,
    file_subtree_signature,
    path_key,
    project_overlay_signature,
    resolver_signature,
)

logger = logging.getLogger(__name__)

# Порог диагностики: операции резолва длиннее этого пишем в лог,
# чтобы было видно, где именно теряются секунды при открытии файла.
_SLOW_KEY_SECONDS = 0.25
_SLOW_CACHE_SECONDS = 0.25


def _accepts_overlay(func) -> bool:
    """Поддерживает ли адаптер необязательный аргумент ``overlay``."""
    try:
        parameters = inspect.signature(func).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == "overlay"
        or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _filter_overlay(forest, root_path, overlay):
    """Оставить в overlay только правки, входящие в поддерево root_path.

    Ключ resolved-кэша считается по overlay. Если не отфильтровать
    ``source_files``, правка одного файла меняет ключ **всех** корней, и
    кэш пересобирается целиком даже при точечной инвалидации.
    """
    if overlay is None:
        return None

    has_attrs = hasattr(overlay, "source_files") or hasattr(
        overlay,
        "resolved_edits",
    )
    source_files = getattr(overlay, "source_files", None)
    if source_files is None and not has_attrs:
        # Плоский словарь {path: text} (overlay сборки/тестов).
        source_files = overlay
        resolved = {}
    else:
        source_files = source_files or {}
        resolved = getattr(overlay, "resolved_edits", None) or {}

    root_key = path_key(root_path)
    include_parents = getattr(forest, "include_parents", {}) or {}
    node_parent = getattr(forest, "node_parent", {}) or {}

    memo = {}

    def belongs(path):
        key = path_key(path)
        if key == root_key:
            return True
        cached = memo.get(key)
        if cached is not None:
            return cached

        stack = [Path(path)]
        seen = set()
        found = False
        while stack:
            node = stack.pop()
            node_key = path_key(node)
            if node_key == root_key:
                found = True
                break
            if node_key in seen:
                continue
            seen.add(node_key)

            parents = include_parents.get(node)
            if not parents:
                parents = include_parents.get(node_key)
            if parents:
                stack.extend(Path(parent) for parent in parents)
                continue

            parent = node_parent.get(node)
            if parent is None:
                parent = node_parent.get(node_key)
            if parent is not None:
                stack.append(Path(parent))

        memo[key] = found
        return found

    filtered_sources = {
        Path(path): text
        for path, text in source_files.items()
        if belongs(path)
    }
    filtered_resolved = {
        Path(path): value
        for path, value in resolved.items()
        if belongs(path)
    }

    if not filtered_sources and not filtered_resolved:
        return None

    if not has_attrs:
        return filtered_sources

    filtered = SimpleNamespace(
        source_files=filtered_sources,
        resolved_edits=filtered_resolved,
    )
    # Фильтрация детерминирована: для того же исходного overlay и корня
    # подпись не нужно пересчитывать по 40-МБ текстам.
    revision = getattr(overlay, "revision", None)
    if revision is not None:
        filtered.revision = "%s:%s" % (revision, root_key)
    return filtered


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
        self._adapter_context = None
        self._adapter_signature = None
        self._inflight_lock = threading.RLock()
        self._inflight = {}
        self._last_keys = {}

        # Ключ резолва содержит подпись всего поддерева (stat всех
        # include/DLTX-файлов). Для system.ltx это тысячи файлов, поэтому
        # повторно считать подпись при каждом открытии нельзя: кэшируем
        # готовый ключ на время жизни дерева. Сбрасывается вместе с
        # ResolvedCache при «Пересборке VFS».
        self._key_cache = {}
        self._key_lock = threading.RLock()

    def invalidate_cache(self):
        """Сбросить кэш резолва (вызывается при «Пересборке VFS»)."""
        self._resolved_cache.clear()
        with self._key_lock:
            self._key_cache.clear()

    def invalidate_roots(self, roots):
        """Точечно сбросить ключи/значения для набора корней."""
        wanted = {path_key(path) for path in (roots or ())}
        if not wanted:
            return
        with self._key_lock:
            for identity in list(self._key_cache):
                # identity = (id(forest), kind, root_key, overlay_key)
                if len(identity) > 2 and identity[2] in wanted:
                    self._key_cache.pop(identity, None)
        discard = getattr(self._resolved_cache, "discard", None)
        if callable(discard):
            discard(
                lambda key: (
                    isinstance(key, tuple)
                    and len(key) > 2
                    and key[2] in wanted
                )
            )

    def cache_stats(self):
        return self._resolved_cache.stats()

    def overlay_for_root(self, forest, root_path, overlay):
        """Overlay, относящийся к поддереву конкретного корня.

        Один и тот же снимок должен использоваться и для ключа кэша, и
        для вычисления значения. Иначе resolved-правки, не входящие в
        поддерево, меняют ключ, но не результат (или наоборот).
        """
        return _filter_overlay(forest, root_path, overlay)

    # -------------------------------------------------- адаптер

    def _ensure_adapter(self, forest, config_dir):
        """Найти адаптер; пересоздать при смене VFS или Resolver4.py."""
        # Адаптер, установленный снаружи (тесты/альтернативный
        # композиционный корень), не пересоздаём: у него нет контекста.
        if self._adapter is not None and self._adapter_context is None:
            return self._adapter

        scan_dir = getattr(forest, "scan_dir", None)
        context = (
            path_key(scan_dir) if scan_dir else "",
            path_key(config_dir) if config_dir else "",
        )

        bases = build_search_bases(scan_dir, config_dir)
        script = find_resolver_script(bases)
        if script is None:
            logger.warning("Resolver4.py не найден в базах: %s", bases)
            self._adapter = None
            self._adapter_context = None
            self._adapter_signature = None
            return None

        signature = resolver_signature(script)
        if (
            self._adapter is not None
            and self._adapter_context == context
            and self._adapter_signature == signature
        ):
            return self._adapter

        self._adapter = LtxResolverAdapter(script, cache=self._cache)
        self._adapter_context = context
        self._adapter_signature = signature
        logger.info("Resolver4 подключён: %s", self._adapter.resolver_script)
        return self._adapter

    def get_adapter(self):
        """Вернуть текущий адаптер (или None)."""
        return self._adapter

    def ensure_adapter(self, forest, config_dir):
        """Публичная инициализация адаптера перед расчётом ключей кэша.

        Ключ содержит подпись Resolver4.py, поэтому адаптер должен быть
        найден до первого ``resolved_key()``.
        """
        return self._ensure_adapter(forest, config_dir)

    def _resolved_key(self, kind, root_path, forest, config_dir,
                      overlay=None):
        """Ключ кэша: результат зависит от файла и его зависимостей.

        Подпись поддерева включает mtime+size самого файла, include-файлов
        и присоединённых mod_*.ltx, а также подпись Resolver4.py.

        ``config_dir`` намеренно не входит в ключ: он влияет только на
        поиск Resolver4.py (уже покрыт подписью скрипта), а разные точки
        вызова передают то ``configs``, то ``scan_dir``.

        Готовый ключ кэшируется на время жизни дерева: подпись поддерева
        может стоить секунды (system.ltx включает тысячи файлов), а
        открытие уже собранного файла не должно её пересчитывать.
        """
        root_key = path_key(root_path)
        overlay_key = project_overlay_signature(overlay)
        identity = (
            str(getattr(forest, "cache_id", id(forest))),
            kind,
            root_key,
            overlay_key,
        )

        with self._key_lock:
            cached = self._key_cache.get(identity)
        if cached is not None:
            return cached

        started = time.perf_counter()
        key = (
            # v4: line_sources хранятся как ссылки на общие LineSource.
            # Старые записи (v3) содержат по объекту на строку и читаются
            # с диска секундами, поэтому их нужно пересобрать.
            "resolve-v4",
            kind,
            root_key,
            file_subtree_signature(forest, root_path),
            resolver_signature(
                getattr(self._adapter, "resolver_script", None)
            ),
            overlay_key,
        )

        with self._key_lock:
            self._key_cache[identity] = key

        elapsed = time.perf_counter() - started
        if elapsed >= _SLOW_KEY_SECONDS:
            logger.warning(
                "resolver key: %.2f s для %s (подпись поддерева)",
                elapsed,
                Path(root_path).name,
            )
        return key

    def _cache_call(self, kind, root_path, forest, config_dir,
                    compute, log=None, overlay=None):
        """Кэш + дедупликация одновременного резолва одного файла.

        Пользователь может открыть файл, который уже резолвится фоновой
        предсборкой: второй поток дождётся результата, а не запустит
        повторный проход.
        """
        key = self._resolved_key(
            kind,
            root_path,
            forest,
            config_dir,
            overlay=overlay,
        )
        return self._cache_call_key(key, root_path, compute, log=log)

    def _cache_call_key(self, key, root_path, compute, log=None):
        """То же, но ключ уже посчитан (предсборка не делает stat дважды)."""
        identity = None
        previous = None
        if isinstance(key, tuple) and len(key) >= 3:
            identity = (key[1], key[2])
            with self._inflight_lock:
                previous = self._last_keys.get(identity)
                self._last_keys[identity] = key

        started = time.perf_counter()
        cached = self._resolved_cache.get(key)
        load_elapsed = time.perf_counter() - started
        if load_elapsed >= _SLOW_CACHE_SECONDS:
            logger.warning(
                "resolver cache: %.2f s на чтение %s",
                load_elapsed,
                Path(root_path).name,
            )
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
            try:
                cached = self._resolved_cache.get(key, count_miss=False)
                if cached is not None:
                    if log:
                        log("resolver cache: hit %s" % Path(root_path).name)
                    return cached

                if log:
                    log(
                        "resolver cache: miss %s (%s)"
                        % (
                            Path(root_path).name,
                            miss_reason(
                                previous,
                                key,
                                ("version", "kind", "path", "signature", "resolver"),
                            ),
                        )
                    )
                started = time.perf_counter()
                value = compute()
                compute_elapsed = time.perf_counter() - started
                if compute_elapsed >= _SLOW_CACHE_SECONDS:
                    logger.warning(
                        "resolver compute: %.2f s для %s",
                        compute_elapsed,
                        Path(root_path).name,
                    )
                self._resolved_cache.set(key, value)
                return value
            finally:
                with self._inflight_lock:
                    if self._inflight.get(key) is key_lock:
                        self._inflight.pop(key, None)

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
        return self.has_resolved_key(
            self.resolved_key(root_path, forest, config_dir)
        )

    def has_resolved_key(self, key):
        """Проверка готовности по уже посчитанному ключу."""
        return self._resolved_cache.has(key)

    def resolved_key(self, root_path, forest, config_dir, overlay=None):
        """Публичный ключ resolved-кэша для предсборки."""
        return self._resolved_key(
            "resolved",
            root_path,
            forest,
            config_dir,
            overlay=overlay,
        )

    def store_resolved(self, root_path, forest, config_dir, value, key=None,
                       overlay=None):
        """Положить результат фоновой сборки в кэш."""
        if key is None:
            key = self.resolved_key(
                root_path,
                forest,
                config_dir,
                overlay=overlay,
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

    def assemble_text(self, root_path, forest, config_dir, log=None,
                      overlay=None):
        """
        Собрать финальный текст конфигурации для файла.

        Это бывший метод фасада `assemble_text`.

        Args:
            root_path: Путь к корневому файлу.
            forest: Построенное дерево файлов.
            config_dir: Каталог конфигов.
            log: Опциональная функция логирования.
            overlay: Черновики проекта ``{путь: текст}``.

        Returns:
            str: Итоговый текст конфигурации.

        Raises:
            RuntimeError: Если резолвер не найден.
        """
        adapter = self._ensure_adapter(forest, config_dir)
        if adapter is None:
            raise RuntimeError("Resolver4.py не найден")

        overlay = self.overlay_for_root(forest, root_path, overlay)
        supports_overlay = overlay is not None and _accepts_overlay(
            adapter.assemble_text
        )

        def compute():
            sm = build_source_map(forest)
            kwargs = {}
            if supports_overlay:
                kwargs["overlay"] = overlay
                kwargs["log"] = log
            return (
                adapter.assemble_text(
                    root_path,
                    sm,
                    **kwargs,
                ),
                None,
            )

        return self._cache_call(
            "text",
            root_path,
            forest,
            config_dir,
            compute,
            log=log,
            overlay=overlay,
        )[0]

    def assemble_resolved(self, root_path, forest, config_dir, log=None,
                          key=None, overlay=None):
        adapter = self._ensure_adapter(forest, config_dir)
        if adapter is None:
            raise RuntimeError("Resolver4.py не найден")

        overlay = self.overlay_for_root(forest, root_path, overlay)
        supports_overlay = overlay is not None and _accepts_overlay(
            adapter.assemble_resolved
        )

        def compute():
            sm, abs_map = self.source_maps(forest)
            kwargs = {}
            if supports_overlay:
                kwargs["overlay"] = overlay
                kwargs["log"] = log
            return adapter.assemble_resolved(
                root_path,
                sm,
                abs_map,
                **kwargs,
            )

        if key is not None:
            return self._cache_call_key(key, root_path, compute, log=log)

        return self._cache_call(
            "resolved",
            root_path,
            forest,
            config_dir,
            compute,
            log=log,
            overlay=overlay,
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

        out = out_dir / (
            "%s__%s__assembled.ltx"
            % (
                safe_filename_component(rp.parent.name),
                safe_filename_component(rp.stem),
            )
        )
        out.write_text(text, encoding="utf-8")
        return out