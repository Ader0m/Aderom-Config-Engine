# -*- coding: utf-8 -*-
"""Фоновая предсборка резолвера.

Запускается после построения дерева и складывает в общий ResolvedCache
(память + диск):

- LTX-корни (файлы, которые дают итоговые конфиги); include-файлы
  отдельно не резолвятся, их содержимое уже входит в итоговый текст корня;
- все XML-файлы леса (include-сборка + DXML), иначе поиск по XML
  в графе связей каждый раз читает и разбирает их заново.

Для многоядерной сборки используется пул процессов: Resolver4 и
XmlResolver — Python-код, поэтому потоки не дают ускорения из-за GIL.
По умолчанию берётся ~75% логических процессоров, но всегда оставляется
хотя бы один для UI/ОС.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import threading
import time
from pathlib import Path

from core.tools.parallel import default_workers

logger = logging.getLogger(__name__)

_WORKER_STATE = {}


def resolved_ltx_roots(forest):
    """LTX-корни леса без XML и без дублей.

    Только корни дают итоговые конфиги. Include-файлы отдельно не
    резолвятся: их содержимое уже входит в текст корня.
    """
    seen = set()
    out = []

    for path in (getattr(forest, "roots", ()) or ()):
        path = Path(path)
        if path.suffix.lower() != ".ltx":
            continue
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(path)

    return out


def _default_workers():
    return default_workers("ACE_PREBUILD_WORKERS")


def _init_worker(resolver_script, source_map, abs_map):
    """Инициализация процесса-воркера (один раз на процесс)."""
    from core.resolver.ltx import LtxResolverAdapter

    _WORKER_STATE["adapter"] = LtxResolverAdapter(
        resolver_script,
        cache=None,
    )
    _WORKER_STATE["source_map"] = source_map
    _WORKER_STATE["abs_map"] = abs_map


def _worker_resolve(job):
    """Резолв одного LTX-корня в дочернем процессе."""
    root_path, key, overlay = job
    try:
        value = _WORKER_STATE["adapter"].assemble_resolved(
            str(root_path),
            _WORKER_STATE["source_map"],
            _WORKER_STATE["abs_map"],
            overlay=overlay,
        )
        return root_path, key, value, None
    except Exception as exc:
        return root_path, key, None, str(exc)


def _xml_overlay(overlay):
    """Оставить в overlay только XML-черновики проекта."""
    source_files = getattr(overlay, "source_files", None)
    if source_files is None:
        return overlay if isinstance(overlay, dict) and overlay else None
    filtered = {
        Path(path): str(text or "")
        for path, text in source_files.items()
        if Path(path).suffix.lower() == ".xml"
    }
    return filtered or None


def _init_xml_worker(config_dir, overlay=None):
    """Инициализация XML-резолвера в процессе-воркере."""
    from core.xml.resolver import XmlResolver

    _WORKER_STATE["xml"] = XmlResolver(config_dir=config_dir)
    _WORKER_STATE["xml_overlay"] = overlay


def _worker_resolve_xml(job):
    """Резолв одного XML-файла в дочернем процессе."""
    path, key = job
    try:
        value = _WORKER_STATE["xml"].resolve_output(
            path,
            overlay=_WORKER_STATE.get("xml_overlay"),
        )
        return path, key, value, None
    except Exception as exc:
        return path, key, None, str(exc)


class PrebuildService:
    """Фоновая предсборка LTX-корней и XML-файлов."""

    def __init__(self, resolver, xml_resolver=None, workers=None):
        self._resolver = resolver
        self._xml_resolver = xml_resolver
        self._workers = workers
        self._lock = threading.RLock()
        self._thread = None
        self._pool = None
        self._stop = threading.Event()

        self._state = "idle"      # idle | running | done
        self._done = 0
        self._total = 0
        self._error = None

    # -------------------------------------------------- управление
    def start(self, forest, config_dir, overlay=None, roots=None):
        """Запустить предсборку.

        ``roots`` — необязательный набор корней. Для точечной инвалидации
        после проекта пересобираются только они; XML-файлы при этом не
        трогаются, потому что overlay проекта в XML-резолв не входит.
        """
        with self._lock:
            old = self._thread
            self._stop.set()
            old_pool = self._pool

        if old_pool is not None:
            try:
                old_pool.terminate()
            except Exception:
                pass

        if old is not None and old.is_alive():
            old.join(timeout=0.5)

        ltx_targets = resolved_ltx_roots(forest)
        if roots is not None:
            wanted = {str(Path(path)).lower() for path in roots}
            ltx_targets = [
                path for path in ltx_targets
                if str(path).lower() in wanted
            ]
        xml_targets = []
        if roots is None and self._xml_resolver is not None:
            if hasattr(self._xml_resolver, "refresh"):
                self._xml_resolver.refresh()
            seen = set()
            for path in (getattr(forest, "xml_files", ()) or ()):
                path = Path(path)
                key = str(path).lower()
                if key in seen:
                    continue
                seen.add(key)
                # Виртуальный XML проекта может ещё не существовать на
                # диске; его разрешение не нужно для каталога предметов.
                if not path.is_file():
                    continue
                xml_targets.append(path)

        def _size(path):
            try:
                return path.stat().st_size
            except OSError:
                return 0

        # Мелкие файлы быстрее дают видимый прогресс и заполняют кэш.
        ltx_targets.sort(key=_size)
        xml_targets.sort(key=_size)

        with self._lock:
            stop_event = threading.Event()
            self._stop = stop_event
            self._state = "running"
            self._done = 0
            self._total = len(ltx_targets) + len(xml_targets)
            self._error = None
            self._pool = None
            self._thread = threading.Thread(
                target=self._run,
                args=(
                    forest,
                    config_dir,
                    ltx_targets,
                    xml_targets,
                    stop_event,
                    overlay or {},
                ),
                name="ltx-prebuild",
                daemon=True,
            )
            self._thread.start()

        logger.info(
            "Фоновая сборка запущена: LTX %d, XML %d, процессов: %d%s",
            len(ltx_targets),
            len(xml_targets),
            self._workers or _default_workers(),
            " (только затронутые корни)" if roots is not None else "",
        )

    def stop(self):
        self._stop.set()
        with self._lock:
            pool = self._pool

        if pool is not None:
            try:
                pool.terminate()
            except Exception:
                pass

    def wait(self, log=None, timeout=None):
        """Дождаться окончания фоновой сборки (для графа связей).

        ``timeout`` — защита от вечного ожидания: зависший один корень не
        должен блокировать граф связей навсегда.
        """
        if log:
            log("Ожидание фоновой сборки…")

        deadline = (
            time.monotonic() + float(timeout)
            if timeout is not None
            else None
        )
        while True:
            with self._lock:
                thread = self._thread

            if thread is None or not thread.is_alive():
                break

            if deadline is not None and time.monotonic() >= deadline:
                if log:
                    log(
                        "Фоновая сборка не завершилась за %.0f с; "
                        "граф продолжит работу по кэшу/по требованию"
                        % float(timeout)
                    )
                return

            thread.join(timeout=0.2)

        if log:
            log("Фоновая сборка завершена")

    def status(self):
        with self._lock:
            return {
                "state": self._state,
                "done": self._done,
                "total": self._total,
                "error": self._error,
            }

    # -------------------------------------------------- рабочий поток
    def _run(self, forest, config_dir, ltx_targets, xml_targets,
             stop_event, overlay=None):
        try:
            self._run_ltx(
                forest,
                config_dir,
                ltx_targets,
                stop_event,
                overlay=overlay,
            )
            if not stop_event.is_set():
                self._run_xml(
                    config_dir,
                    xml_targets,
                    stop_event,
                    overlay=overlay,
                )
        except Exception as exc:
            with self._lock:
                if self._stop is stop_event:
                    self._error = str(exc)
            logger.exception("Фоновая сборка прервана")
        finally:
            with self._lock:
                self._pool = None
                if self._stop is stop_event:
                    self._state = (
                        "idle" if stop_event.is_set() else "done"
                    )
                state = self._state
                done = self._done
                total = self._total

            if state == "done":
                logger.info(
                    "Фоновая сборка завершена: %d/%d файлов",
                    done,
                    total,
                )

    def _overlay_for_root(self, forest, path, overlay):
        """Overlay для ключа/значения одного корня.

        У настоящего ResolverService есть фильтрация по поддереву. У
        тестовых/альтернативных реализаций её может не быть — тогда
        используем overlay как есть.
        """
        fn = getattr(self._resolver, "overlay_for_root", None)
        if callable(fn):
            return fn(forest, path, overlay)
        return overlay

    def _run_ltx(self, forest, config_dir, targets, stop_event,
                 overlay=None):
        # Подпись Resolver4.py входит в ключ кэша, поэтому адаптер
        # должен быть найден до расчёта ключей.
        adapter = self._resolver.ensure_adapter(forest, config_dir)

        missing = []
        cached = 0
        for path in targets:
            if stop_event.is_set():
                break
            # Ключ и значение обязаны считаться по одному и тому же
            # overlay. Для каждого корня отбрасываем resolved-правки,
            # которые к его поддереву не относятся.
            root_overlay = self._overlay_for_root(
                forest,
                path,
                overlay,
            )
            key = self._resolver.resolved_key(
                path,
                forest,
                config_dir,
                overlay=root_overlay,
            )
            if self._resolver.has_resolved_key(key):
                cached += 1
                self._advance(stop_event)
            else:
                missing.append((path, key, root_overlay))

        logger.info(
            "Предсборка LTX: кэш %d, вычислить %d из %d",
            cached,
            len(missing),
            len(targets),
        )
        if not missing or stop_event.is_set():
            return

        workers = self._workers or _default_workers()

        def load(job):
            path, key, root_overlay = job
            return self._resolver.assemble_resolved(
                path,
                forest,
                config_dir,
                log=None,
                key=key,
                overlay=root_overlay,
            )

        if workers > 1 and adapter is not None:
            source_map, abs_map = self._resolver.source_maps(forest)

            self._run_parallel(
                missing,
                stop_event,
                workers,
                initializer=_init_worker,
                initargs=(
                    str(adapter.resolver_script),
                    source_map,
                    abs_map,
                ),
                worker=_worker_resolve,
                store=lambda path, key, value: self._resolver.store_resolved(
                    path,
                    forest,
                    config_dir,
                    value,
                    key=key,
                    overlay=None,
                ),
                label="Предсборка",
                fallback=lambda: self._run_sequential(
                    missing,
                    stop_event,
                    load,
                    "Предсборка",
                    break_on_missing_resolver=True,
                ),
            )
        else:
            self._run_sequential(
                missing,
                stop_event,
                load,
                "Предсборка",
                break_on_missing_resolver=True,
            )

    def _run_xml(self, config_dir, targets, stop_event, overlay=None):
        if self._xml_resolver is None or not targets:
            return

        overlay = _xml_overlay(overlay)
        missing = []
        for path in targets:
            if stop_event.is_set():
                break
            key = self._xml_resolver.cache_key(
                path,
                config_dir,
                overlay=overlay,
            )
            if self._xml_resolver.has_resolved_by_key(key):
                self._advance(stop_event)
            else:
                missing.append((path, key))

        if not missing or stop_event.is_set():
            return

        workers = self._workers or _default_workers()

        def load(job):
            path, key = job
            return self._xml_resolver.resolve_output(
                path,
                config_dir,
                log=None,
                key=key,
                overlay=overlay,
            )

        if workers > 1:
            canonical = config_dir
            if hasattr(self._xml_resolver, "effective_config_dir"):
                canonical = self._xml_resolver.effective_config_dir(
                    config_dir
                )

            self._run_parallel(
                missing,
                stop_event,
                workers,
                initializer=_init_xml_worker,
                initargs=(str(canonical), overlay),
                worker=_worker_resolve_xml,
                store=lambda path, key, value: (
                    self._xml_resolver.store_output_by_key(key, value)
                ),
                label="Предсборка XML",
                fallback=lambda: self._run_sequential(
                    missing,
                    stop_event,
                    load,
                    "Предсборка XML",
                ),
            )
        else:
            self._run_sequential(
                missing,
                stop_event,
                load,
                "Предсборка XML",
            )

    def _advance(self, stop_event):
        with self._lock:
            if self._stop is stop_event:
                self._done += 1

    def _run_sequential(
        self,
        jobs,
        stop_event,
        load,
        label,
        break_on_missing_resolver=False,
    ):
        for job in jobs:
            if stop_event.is_set():
                break
            path = job[0]
            try:
                load(job)
            except Exception as exc:
                with self._lock:
                    if self._stop is stop_event:
                        self._error = str(exc)
                logger.warning("%s %s: %s", label, path, exc)
                if (
                    break_on_missing_resolver
                    and "Resolver4.py не найден" in str(exc)
                ):
                    break

            self._advance(stop_event)
            self._log_progress(stop_event, jobs)

    def _run_parallel(
        self,
        missing,
        stop_event,
        workers,
        initializer,
        initargs,
        worker,
        store,
        label,
        fallback,
    ):
        jobs = [
            (str(path), key, *rest)
            for path, key, *rest in missing
        ]
        pool = None
        try:
            ctx = mp.get_context("spawn")
            try:
                pool = ctx.Pool(
                    processes=min(workers, max(1, len(jobs))),
                    initializer=initializer,
                    initargs=initargs,
                )
            except Exception as exc:
                logger.warning(
                    "Не удалось создать пул процессов (%s), "
                    "перехожу на один поток",
                    exc,
                )
                fallback()
                return

            with self._lock:
                if self._stop is stop_event:
                    self._pool = pool

            for path, key, value, error in pool.imap_unordered(
                worker,
                jobs,
                chunksize=1,
            ):
                if stop_event.is_set():
                    break

                if error is not None:
                    with self._lock:
                        if self._stop is stop_event:
                            self._error = error
                    logger.warning("%s %s: %s", label, path, error)
                elif value is not None:
                    store(path, key, value)

                self._advance(stop_event)
                self._log_progress(stop_event, jobs)
        finally:
            self._close_pool(pool, stop_event)

    @staticmethod
    def _close_pool(pool, stop_event):
        if pool is None:
            return
        try:
            if stop_event.is_set():
                pool.terminate()
            else:
                pool.close()
            pool.join()
        except Exception:
            pass

    def _log_progress(self, stop_event, targets):
        with self._lock:
            if self._stop is not stop_event:
                return
            done = self._done
            total = self._total

        if total and (done % 25 == 0 or done == total):
            logger.info("Фоновая сборка: %d/%d", done, total)
