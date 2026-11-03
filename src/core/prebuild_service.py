# -*- coding: utf-8 -*-
"""Фоновая предсборка резолвера.

Запускается после построения дерева, резолвит LTX-корни (файлы, которые
дают итоговые конфиги) и складывает результаты в общий ResolvedCache
(память + диск). Include-файлы отдельно не резолвятся: их содержимое уже
входит в итоговый текст корня.

Для многоядерной сборки используется пул процессов: Resolver4 — Python-код,
поэтому потоки не дают ускорения из-за GIL. По умолчанию берётся ~75%
логических процессоров, но всегда оставляется хотя бы один для UI/ОС.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
import threading

from .resolve_targets import resolved_ltx_roots

logger = logging.getLogger(__name__)

_WORKER_STATE = {}


def _default_workers():
    env = os.environ.get("ACE_PREBUILD_WORKERS")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass

    cpu = os.cpu_count() or 1
    if cpu <= 1:
        return 1
    # Не более 75%, но минимум один логический CPU остаётся системе/UI.
    return max(1, min(cpu - 1, int(cpu * 0.75)))


def _init_worker(resolver_script, source_map, abs_map):
    """Инициализация процесса-воркера (один раз на процесс)."""
    from .resolver_adapter import LtxResolverAdapter

    _WORKER_STATE["adapter"] = LtxResolverAdapter(
        resolver_script,
        cache=None,
    )
    _WORKER_STATE["source_map"] = source_map
    _WORKER_STATE["abs_map"] = abs_map


def _worker_resolve(root_path):
    """Резолв одного корня в дочернем процессе."""
    try:
        value = _WORKER_STATE["adapter"].assemble_resolved(
            str(root_path),
            _WORKER_STATE["source_map"],
            _WORKER_STATE["abs_map"],
        )
        return str(root_path), value, None
    except Exception as exc:
        return str(root_path), None, str(exc)


class PrebuildService:
    """Фоновая предсборка LTX-корней."""

    def __init__(self, resolver, workers=None):
        self._resolver = resolver
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
    def start(self, forest, config_dir):
        """Запустить предсборку для нового дерева (старую остановить)."""
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

        targets = resolved_ltx_roots(forest)

        def _size(path):
            try:
                return path.stat().st_size
            except OSError:
                return 0

        # Мелкие файлы быстрее дают видимый прогресс и заполняют кэш.
        targets.sort(key=_size)

        with self._lock:
            stop_event = threading.Event()
            self._stop = stop_event
            self._state = "running"
            self._done = 0
            self._total = len(targets)
            self._error = None
            self._pool = None
            self._thread = threading.Thread(
                target=self._run,
                args=(forest, config_dir, targets, stop_event),
                name="ltx-prebuild",
                daemon=True,
            )
            self._thread.start()

        logger.info(
            "Фоновая сборка запущена: %d файлов, процессов: %d",
            len(targets),
            self._workers or _default_workers(),
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

    def wait(self, log=None):
        """Дождаться окончания фоновой сборки (для графа связей)."""
        if log:
            log("Ожидание фоновой сборки…")

        while True:
            with self._lock:
                thread = self._thread

            if thread is None or not thread.is_alive():
                break

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
    def _run(self, forest, config_dir, targets, stop_event):
        total = len(targets)

        try:
            missing = []

            # 1. Всё, что уже есть на диске/в памяти, не отправляем воркерам.
            for path in targets:
                if stop_event.is_set():
                    break
                if self._resolver.has_resolved(path, forest, config_dir):
                    with self._lock:
                        if self._stop is stop_event:
                            self._done += 1
                else:
                    missing.append(path)

            # 2. Оставшееся резолвим параллельно.
            if missing and not stop_event.is_set():
                workers = self._workers or _default_workers()
                adapter = self._resolver.get_adapter()

                if workers > 1 and adapter is not None:
                    self._run_parallel(
                        forest,
                        config_dir,
                        missing,
                        stop_event,
                        workers,
                        adapter,
                    )
                else:
                    self._run_sequential(
                        forest,
                        config_dir,
                        missing,
                        stop_event,
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

            if state == "done":
                logger.info("Фоновая сборка завершена: %d файлов", total)

    def _run_sequential(self, forest, config_dir, targets, stop_event):
        for path in targets:
            if stop_event.is_set():
                break
            try:
                self._resolver.assemble_resolved(
                    path,
                    forest,
                    config_dir,
                    log=None,
                )
            except Exception as exc:
                with self._lock:
                    if self._stop is stop_event:
                        self._error = str(exc)
                logger.warning("Предсборка %s: %s", path, exc)
                if "Resolver4.py не найден" in str(exc):
                    break

            with self._lock:
                if self._stop is stop_event:
                    self._done += 1

            self._log_progress(stop_event, targets)

    def _run_parallel(self, forest, config_dir, missing, stop_event,
                      workers, adapter):
        source_map, abs_map = self._resolver.source_maps(forest)
        script = adapter.resolver_script

        pool = None
        try:
            ctx = mp.get_context("spawn")
            try:
                pool = ctx.Pool(
                    processes=min(workers, max(1, len(missing))),
                    initializer=_init_worker,
                    initargs=(str(script), source_map, abs_map),
                )
            except Exception as exc:
                logger.warning(
                    "Не удалось создать пул процессов (%s), "
                    "перехожу на один поток",
                    exc,
                )
                self._run_sequential(
                    forest,
                    config_dir,
                    missing,
                    stop_event,
                )
                return
            with self._lock:
                if self._stop is stop_event:
                    self._pool = pool

            for root_str, value, error in pool.imap_unordered(
                _worker_resolve,
                [str(path) for path in missing],
                chunksize=1,
            ):
                if stop_event.is_set():
                    break

                if error is not None:
                    with self._lock:
                        if self._stop is stop_event:
                            self._error = error
                    logger.warning("Предсборка %s: %s", root_str, error)
                elif value is not None:
                    self._resolver.store_resolved(
                        root_str,
                        forest,
                        config_dir,
                        value,
                    )

                with self._lock:
                    if self._stop is stop_event:
                        self._done += 1

                self._log_progress(stop_event, missing)
        finally:
            if pool is not None:
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
