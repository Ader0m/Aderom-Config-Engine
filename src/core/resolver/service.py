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
    project_overlay_signature,
    resolver_signature,
)
from core.tools.paths import path_key

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

    def __init__(self, cache=None, resolved_cache=None, cache_service=None):
        """
        Args:
            cache: FileCacheSystem — legacy fallback для standalone/тестов.
            resolved_cache: ResolvedCache — legacy fallback для
                standalone/тестов; в production принадлежит CacheService.
        """
        self._cache = cache
        if resolved_cache is None and cache_service is None:
            resolved_cache = ResolvedCache()
        self._resolved_cache = resolved_cache
        self._cache_service = cache_service
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
        if self._cache_service is not None:
            # В production backend'ом владеет CacheService; явные
            # namespace'ы — контракт инвалидации, а не побочный эффект
            # очистки XML-кэша.
            self._cache_service.clear_namespace("resolve.text")
            self._cache_service.clear_namespace("resolve.semantic")
        if self._resolved_cache is not None:
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
                # identity = (snapshot, kind, root_key, overlay_key)
                if len(identity) > 2 and identity[2] in wanted:
                    self._key_cache.pop(identity, None)
        if self._cache_service is not None:
            self._cache_service.invalidate_deps(roots)
            return
        discard = getattr(self._resolved_cache, "discard", None)
        if callable(discard):
            discard(
                lambda key: (
                    isinstance(key, tuple)
                    and len(key) > 3
                    and key[3] in wanted
                )
            )

    def cache_stats(self):
        if self._cache_service is None:
            return self._resolved_cache.stats()
        stats = self._cache_service.stats()
        result = dict(stats.get("backends", {}).get("resolved", {}))
        result.update(
            stats.get("namespaces", {}).get("resolve.text", {})
        )
        return result

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
        snapshot_id = str(getattr(forest, "cache_id", "") or "")
        identity = (
            snapshot_id,
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
            # v5: ключ включает снимок VFS. Старые записи (v4 и ниже)
            # не читаются: кэш пересобирается после смены версии.
            "resolve-v5",
            kind,
            snapshot_id,
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
                    compute, log=None, overlay=None,
                    namespace="resolve.text"):
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
        return self._cache_call_key(
            key,
            root_path,
            compute,
            log=log,
            namespace=namespace,
        )

    def _cache_call_key(self, key, root_path, compute, log=None,
                        namespace="resolve.text"):
        """То же, но ключ уже посчитан (предсборка не делает stat дважды)."""
        identity = None
        previous = None
        if isinstance(key, tuple) and len(key) >= 4:
            identity = (key[1], key[3])
            with self._inflight_lock:
                previous = self._last_keys.get(identity)
                self._last_keys[identity] = key

        if self._cache_service is None and namespace != "resolve.text":
            # Семантическая модель ссылается на объекты Resolver4 и не
            # сериализуется в дисковый кэш. Без CacheService не создаём
            # второй кэш: считаем результат напрямую.
            return compute()

        if self._cache_service is not None:
            snapshot_id = str(key[2] or "") if len(key) > 2 else ""

            def compute_with_log():
                if log:
                    log(
                        "resolver cache: miss %s (%s)"
                        % (
                            Path(root_path).name,
                            miss_reason(
                                previous,
                                key,
                                (
                                    "version",
                                    "kind",
                                    "snapshot",
                                    "path",
                                    "signature",
                                    "resolver",
                                    "overlay",
                                ),
                            ),
                        )
                    )
                started = time.perf_counter()
                value = compute()
                elapsed = time.perf_counter() - started
                if elapsed >= _SLOW_CACHE_SECONDS:
                    logger.warning(
                        "resolver compute: %.2f s для %s",
                        elapsed,
                        Path(root_path).name,
                    )
                return value

            return self._cache_service.get_or_compute(
                namespace,
                key,
                compute_with_log,
                deps=(root_path,),
                snapshot_id=snapshot_id,
            )

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
        if self._cache_service is not None:
            snapshot_id = str(key[2] or "") if len(key) > 2 else ""
            return self._cache_service.has(
                "resolve.text",
                key,
                snapshot_id=snapshot_id,
            )
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
        if self._cache_service is not None:
            snapshot_id = str(key[2] or "") if len(key) > 2 else ""
            self._cache_service.set(
                "resolve.text",
                key,
                value,
                deps=(root_path,),
                snapshot_id=snapshot_id,
            )
            return
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

    def semantic_key(self, root_path, forest, config_dir, overlay=None):
        """Ключ семантической модели: снимок, корень, overlay."""
        return self._resolved_key(
            "semantic",
            root_path,
            forest,
            config_dir,
            overlay=overlay,
        )

    def assemble_semantic(self, root_path, forest, config_dir, log=None,
                          key=None, overlay=None, snapshot_id=None):
        """Собрать ``ResolvedRoot`` и закэшировать его в памяти.

        Тяжёлая операция: вызывающий слой обязан выполнять её вне UI-потока
        (через платформенный task API).
        """
        adapter = self._ensure_adapter(forest, config_dir)
        if adapter is None:
            raise RuntimeError("Resolver4.py не найден")

        overlay = self.overlay_for_root(forest, root_path, overlay)
        supports_overlay = overlay is not None and _accepts_overlay(
            adapter.assemble_semantic
        )

        if key is None:
            key = self.semantic_key(
                root_path,
                forest,
                config_dir,
                overlay=overlay,
            )

        if snapshot_id is None:
            snapshot_id = (
                str(key[2] or "")
                if isinstance(key, tuple) and len(key) > 2
                else str(getattr(forest, "cache_id", "") or "")
            )
        snapshot_id = str(snapshot_id or "")

        def compute():
            sm, abs_map = self.source_maps(forest)
            kwargs = {}
            if supports_overlay:
                kwargs["overlay"] = overlay
                kwargs["log"] = log

            cached_text = None
            resolved_key = None
            if self._cache_service is not None:
                # Ключ текста отличается от ключа модели только kind.
                # Не считаем подпись поддерева второй раз.
                if isinstance(key, tuple) and len(key) >= 3:
                    resolved_key = (key[0], "resolved") + tuple(key[2:])
                else:
                    resolved_key = self._resolved_key(
                        "resolved",
                        root_path,
                        forest,
                        config_dir,
                        overlay=overlay,
                    )
                cached_text = self._cache_service.get(
                    "resolve.text",
                    resolved_key,
                    snapshot_id=snapshot_id,
                )

            if cached_text is not None:
                text, _cached_sources = cached_text
            else:
                text = None

            root = adapter.assemble_semantic(
                root_path,
                sm,
                abs_map,
                snapshot_id=snapshot_id,
                text=text,
                **kwargs,
            )

            if cached_text is None and self._cache_service is not None:
                # Один текст на корень: следующий открытый редактор берёт
                # его из resolve.text, а не создаёт вторую копию.
                self.store_resolved(
                    root_path,
                    forest,
                    config_dir,
                    (root.text, list(root.line_sources)),
                    key=resolved_key,
                    overlay=overlay,
                )
            return root

        return self._cache_call_key(
            key,
            root_path,
            compute,
            log=log,
            namespace="resolve.semantic",
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