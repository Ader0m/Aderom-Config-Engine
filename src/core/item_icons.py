# -*- coding: utf-8 -*-
"""Загрузка иконок предметов из DDS-атласов.

Тяжёлая часть — декодирование DXT/несжатых DDS. Это Python-код, поэтому
потоки не дают ускорения из-за GIL. По аналогии с предсборкой LTX/XML
используется пул процессов (``spawn``), а результаты складываются в
двухуровневый кэш:

- память — LRU регионов ``TextureRegion``;
- диск — один gzip+pickle-пакет на снимок каталога предметов.

Горячий путь (память) не трогает диск. Дисковый пакет читается один раз
при первом открытии окна после перезапуска приложения.
"""

from __future__ import annotations

import hashlib
import logging
import multiprocessing as mp
import os
import sys

from .dds import TextureRegion, read_dds_region
from .object_cache import ObjectCache
from .resolved_cache import file_stamp, path_key


logger = logging.getLogger(__name__)

# Меняется при изменении правил/формата кэша иконок.
ICON_CACHE_VERSION = 1

_MEMORY_ENTRIES = 4096
_MEMORY_BYTES = 128 * 1024 * 1024
_MAX_DEFAULT_WORKERS = 8


def _default_workers():
    env = os.environ.get("ACE_ITEM_ICON_WORKERS")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass

    cpu = os.cpu_count() or 1
    if cpu <= 1:
        return 1

    # Иконки — мелкие задачи; слишком большой пул тратит время на spawn.
    return min(
        _MAX_DEFAULT_WORKERS,
        max(1, min(cpu - 1, int(cpu * 0.75))),
    )


def _can_spawn():
    """Можно ли безопасно поднять ``spawn``-пул.

    Из ``python -c``/stdin и из замороженного приложения дочерний процесс
    не сможет импортировать ``__main__`` и зависнет навсегда. В этом
    случае лучше сразу декодировать последовательно.
    """
    if getattr(sys, "frozen", False):
        return False

    main = sys.modules.get("__main__")
    path = getattr(main, "__file__", None)
    if not path:
        return False
    if str(path).startswith("<"):
        return False
    return True


def _worker_decode_icon(job):
    """Декодировать один регион в дочернем процессе."""
    section, path, x, y, width, height, key = job
    try:
        region = read_dds_region(path, x, y, width, height)
        return (
            section,
            key,
            region.width,
            region.height,
            region.rgba,
            None,
        )
    except Exception as exc:
        return section, key, 0, 0, b"", str(exc)


class ItemIconService:
    """Декодирует иконки предметов с кэшем и пулом процессов."""

    def __init__(self, cache_dir=None, workers=None):
        self._workers = workers
        self._memory = ObjectCache(
            max_entries=_MEMORY_ENTRIES,
            max_bytes=_MEMORY_BYTES,
            size_fn=_region_size,
        )
        self._bundles = ObjectCache(
            cache_dir=cache_dir,
            max_entries=2,
        )

    # -------------------------------------------------- одиночная иконка
    def read_region(self, path, x, y, width, height):
        """Регион из памяти или декодирование одного DDS-прямоугольника."""
        key = self._icon_key(path, x, y, width, height)
        cached = self._memory.get(key)
        if cached is not None:
            return cached

        region = read_dds_region(path, x, y, width, height)
        self._memory.set(key, region)
        return region

    # -------------------------------------------------- пакетная загрузка
    def load(self, catalog_key, requests, log=None):
        """Загрузить иконки для списка ``(section, path, x, y, w, h)``.

        Возвращает кортеж ``(section, TextureRegion)`` в порядке запросов.
        Ошибки отдельных иконок не прерывают загрузку: для них модуль
        покажет заглушку.
        """
        requests = list(requests or ())
        if not requests:
            return ()

        result = {}
        missing = []
        stamps = {}

        for section, path, x, y, width, height in requests:
            key = self._icon_key(
                path,
                x,
                y,
                width,
                height,
                stamps=stamps,
            )
            cached = self._memory.get(key)
            if cached is not None:
                result[section] = cached
            else:
                missing.append((
                    section,
                    str(path),
                    int(x),
                    int(y),
                    int(width),
                    int(height),
                    key,
                ))

        bundle_key = None
        bundle = {}

        if missing:
            bundle_key = self._bundle_key(catalog_key, requests, stamps)
            loaded = self._bundles.get(bundle_key)
            if isinstance(loaded, dict):
                bundle = loaded

            still_missing = []
            for job in missing:
                section, _path, _x, _y, _w, _h, key = job
                region = bundle.get(key)
                if region is None:
                    still_missing.append(job)
                else:
                    self._memory.set(key, region)
                    result[section] = region
            missing = still_missing

        if missing and log and result:
            log("Предметы: иконки из кэша: %d" % len(result))

        if missing:
            if log:
                workers = self._workers or _default_workers()
                if not _can_spawn():
                    workers = 1
                log(
                    "Предметы: декодирование иконок %d (процессов: %d)…"
                    % (len(missing), workers)
                )

            decoded, errors = self._decode(missing, log)
            for section, key, region in decoded:
                self._memory.set(key, region)
                result[section] = region
                bundle[key] = region

            if bundle_key is not None and decoded:
                self._bundles.set(bundle_key, bundle)

            if errors and log:
                log(
                    "Предметы: не удалось декодировать иконок: %d"
                    % errors
                )

        return tuple(
            (section, result[section])
            for section, _path, _x, _y, _w, _h in requests
            if section in result
        )

    # -------------------------------------------------- декодирование
    def _decode(self, jobs, log):
        total = len(jobs)
        workers = self._workers or _default_workers()

        if workers > 1 and total >= 4 and _can_spawn():
            try:
                decoded, errors = self._decode_parallel(
                    jobs,
                    workers,
                    total,
                    log,
                )
                if errors and not decoded:
                    # Например, дочерний процесс не видит VFS MO2.
                    # В родительском процессе путь должен читаться.
                    if log:
                        log(
                            "Предметы: пул не прочитал иконки, "
                            "повторяю в основном процессе"
                        )
                    return self._decode_sequential(jobs, log)
                return decoded, errors
            except Exception as exc:
                if log:
                    log(
                        "Предметы: пул процессов недоступен (%s), "
                        "декодирую последовательно" % exc
                    )

        return self._decode_sequential(jobs, log)

    def _decode_parallel(self, jobs, workers, total, log):
        ctx = mp.get_context("spawn")
        pool = ctx.Pool(processes=min(workers, max(1, len(jobs))))
        decoded = []
        errors = 0
        done = 0
        failed = False

        try:
            # Как в PrebuildService: обычный for по imap_unordered.
            # Никаких iterator.next() — в разных окружениях возвращается
            # либо IMapIterator, либо generator.
            for result in pool.imap_unordered(
                _worker_decode_icon,
                jobs,
                chunksize=8,
            ):
                section, key, width, height, rgba, error = result
                done += 1

                if error is None and width > 0 and height > 0:
                    decoded.append((
                        section,
                        key,
                        _make_region(width, height, rgba),
                    ))
                else:
                    errors += 1
                    logger.debug("Иконка %s: %s", section, error)

                self._log_progress(log, done, total)
        except BaseException:
            failed = True
            raise
        finally:
            try:
                if failed:
                    pool.terminate()
                else:
                    pool.close()
                pool.join()
            except Exception:
                try:
                    pool.terminate()
                except Exception:
                    pass

        return decoded, errors

    def _decode_sequential(self, jobs, log):
        decoded = []
        errors = 0
        total = len(jobs)

        for index, job in enumerate(jobs, 1):
            section, path, x, y, width, height, key = job
            try:
                region = read_dds_region(path, x, y, width, height)
                decoded.append((section, key, region))
            except Exception as exc:
                errors += 1
                logger.debug("Иконка %s: %s", section, exc)

            self._log_progress(log, index, total)

        return decoded, errors

    @staticmethod
    def _log_progress(log, done, total):
        if not log or not total:
            return
        step = max(1, total // 20)
        if done % step == 0 or done == total:
            log("item progress: %d/%d" % (done, total))

    # -------------------------------------------------- ключи
    @staticmethod
    def _icon_key(path, x, y, width, height, stamps=None):
        pkey = path_key(path)
        if stamps is not None:
            stamp = stamps.get(pkey)
            if stamp is None:
                stamp = file_stamp(path)
                stamps[pkey] = stamp
        else:
            stamp = file_stamp(path)

        return (
            pkey,
            stamp,
            int(x),
            int(y),
            int(width),
            int(height),
        )

    @staticmethod
    def _bundle_key(catalog_key, requests, stamps):
        # Ключ зависит только от DDS-файлов и версии декодера: правка
        # system.ltx не должна заставлять заново декодировать иконки.
        unique = {}
        for _section, path, _x, _y, _w, _h in requests:
            pkey = path_key(path)
            unique[pkey] = stamps.get(pkey) or file_stamp(path)

        parts = ["%s=%s" % item for item in sorted(unique.items())]
        digest = hashlib.sha1(
            ("%s|%s" % (ICON_CACHE_VERSION, "\n".join(parts))).encode(
                "utf-8",
                "replace",
            )
        ).hexdigest()

        return ("item_icons", digest)

    def clear_memory(self):
        self._memory.clear()


# ---------------------------------------------------------------- утилиты


def _make_region(width, height, rgba):
    return TextureRegion(width=int(width), height=int(height), rgba=rgba)


def _region_size(region):
    return int(getattr(region, "width", 0)) * int(
        getattr(region, "height", 0)
    ) * 4
