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

from core.api.graphics import TextureRegion, read_dds_region
from core.api.cache import ObjectCache, file_stamp
from core.api.parallel import can_spawn_processes, default_workers
from core.api.paths import path_key


logger = logging.getLogger(__name__)

# Меняется при изменении правил/формата кэша иконок.
ICON_CACHE_VERSION = 2

ITEM_ICON_REGION_NAMESPACE = "item.icons.region"
ITEM_ICON_BUNDLE_NAMESPACE = "item.icons.bundle"

_MEMORY_ENTRIES = 4096
_MEMORY_BYTES = 128 * 1024 * 1024
_MAX_DEFAULT_WORKERS = 8


def _default_workers():
    # Иконки — мелкие задачи; слишком большой пул тратит время на spawn.
    return default_workers(
        "ACE_ITEM_ICON_WORKERS",
        max_workers=_MAX_DEFAULT_WORKERS,
    )


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

    def __init__(
        self,
        cache_dir=None,
        workers=None,
        max_disk_bytes=None,
        cache_service=None,
    ):
        self._workers = workers
        if cache_service is None:
            raise ValueError(
                "ItemIconService требует общий CacheService"
            )
        self._cache_service = cache_service
        self._memory = ObjectCache(
            max_entries=_MEMORY_ENTRIES,
            max_bytes=_MEMORY_BYTES,
            size_fn=_region_size,
        )
        self._bundles = ObjectCache(
            cache_dir=cache_dir,
            max_entries=2,
            max_disk_bytes=max_disk_bytes,
        )
        self._cache_service.register_backend(
            ITEM_ICON_REGION_NAMESPACE,
            self._memory,
        )
        self._cache_service.register_backend(
            ITEM_ICON_BUNDLE_NAMESPACE,
            self._bundles,
        )

    # -------------------------------------------------- одиночная иконка
    def read_region(self, path, x, y, width, height):
        """Регион из памяти или декодирование одного DDS-прямоугольника."""
        key = self._icon_key(path, x, y, width, height)
        return self._cache_service.get_or_compute(
            ITEM_ICON_REGION_NAMESPACE,
            key,
            lambda: read_dds_region(path, x, y, width, height),
            snapshot_id=self._key_snapshot(key),
        )

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
            cached = self._cache_service.get(
                ITEM_ICON_REGION_NAMESPACE,
                key,
                snapshot_id=self._key_snapshot(key),
            )
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
            if log and result:
                log("Предметы: иконки из кэша: %d" % len(result))

            computed_here = False

            def compute_bundle():
                nonlocal computed_here
                computed_here = True
                decoded = self._decode_missing(missing, log)
                if not decoded:
                    return None
                return {
                    key: region
                    for _section, key, region in decoded
                }

            loaded = self._cache_service.get_or_compute(
                ITEM_ICON_BUNDLE_NAMESPACE,
                bundle_key,
                compute_bundle,
                snapshot_id=self._key_snapshot(bundle_key),
            )
            bundle = loaded if isinstance(loaded, dict) else {}

            still_missing = []
            for job in missing:
                section, _path, _x, _y, _w, _h, key = job
                region = bundle.get(key)
                if region is None:
                    still_missing.append(job)
                    continue
                self._cache_service.set(
                    ITEM_ICON_REGION_NAMESPACE,
                    key,
                    region,
                    snapshot_id=self._key_snapshot(key),
                )
                result[section] = region

            # Bundle — ускоритель, а не полный набор: если в нём нет
            # запрошенных регионов (старый частичный bundle или новый
            # предмет на том же атласе), догружаем их.
            if still_missing and not computed_here:
                decoded = self._decode_missing(still_missing, log)
                for section, key, region in decoded:
                    bundle[key] = region
                    self._cache_service.set(
                        ITEM_ICON_REGION_NAMESPACE,
                        key,
                        region,
                        snapshot_id=self._key_snapshot(key),
                    )
                    result[section] = region
                if decoded:
                    self._cache_service.set(
                        ITEM_ICON_BUNDLE_NAMESPACE,
                        bundle_key,
                        bundle,
                        snapshot_id=self._key_snapshot(bundle_key),
                    )

        return tuple(
            (section, result[section])
            for section, _path, _x, _y, _w, _h in requests
            if section in result
        )

    def _decode_missing(self, jobs, log):
        if log:
            workers = self._workers or _default_workers()
            if not can_spawn_processes():
                workers = 1
            log(
                "Предметы: декодирование иконок %d (процессов: %d)…"
                % (len(jobs), workers)
            )
        decoded, errors = self._decode(jobs, log)
        if errors and log:
            log(
                "Предметы: не удалось декодировать иконок: %d"
                % errors
            )
        return decoded

    # -------------------------------------------------- декодирование
    def _decode(self, jobs, log):
        total = len(jobs)
        workers = self._workers or _default_workers()

        if workers > 1 and total >= 4 and can_spawn_processes():
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

    @staticmethod
    def _key_snapshot(key):
        # В ключе уже есть file stamp (регион) или digest DDS-набора
        # (пакет); версия правил входит в снимок, а не только в формат
        # backend'а.
        return "v%d:%s" % (ICON_CACHE_VERSION, key[1])

    def clear_memory(self):
        self._cache_service.clear_namespace(ITEM_ICON_REGION_NAMESPACE)


# ---------------------------------------------------------------- утилиты


def _make_region(width, height, rgba):
    return TextureRegion(width=int(width), height=int(height), rgba=rgba)


def _region_size(region):
    return int(getattr(region, "width", 0)) * int(
        getattr(region, "height", 0)
    ) * 4
