# -*- coding: utf-8 -*-
"""Тесты пакетной загрузки иконок и кэша регионов."""

import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.support import SRC  # noqa: F401

from app.features.items.icons import (
    ITEM_ICON_BUNDLE_NAMESPACE,
    ItemIconService,
)
from core.cache.service import CacheService


def _dds_rgba(width, height, pixels):
    header = bytearray(128)
    header[0:4] = b"DDS "
    struct.pack_into("<7I", header, 4, 124, 0, height, width, width * 4, 0, 1)
    struct.pack_into(
        "<8I",
        header,
        76,
        32,
        0x41,
        0,
        32,
        0x000000FF,
        0x0000FF00,
        0x00FF0000,
        0xFF000000,
    )
    return bytes(header) + bytes(pixels)


_ICON_SERVICE = ItemIconService


def _service(**kwargs):
    kwargs.setdefault("cache_service", CacheService())
    return _ICON_SERVICE(**kwargs)


class ItemIconTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.dds = self.root / "icons.dds"

        # 2×2: красный, зелёный / синий, белый.
        pixels = [
            255, 0, 0, 255,
            0, 255, 0, 255,
            0, 0, 255, 255,
            255, 255, 255, 255,
        ]
        self.dds.write_bytes(_dds_rgba(2, 2, pixels))

    def tearDown(self):
        self._tmp.cleanup()

    def test_loads_regions_in_request_order(self):
        service = _service(workers=1)
        requests = [
            ("first", self.dds, 0, 0, 1, 1),
            ("second", self.dds, 1, 0, 1, 1),
        ]

        result = service.load("catalog", requests)

        self.assertEqual([section for section, _ in result], ["first", "second"])
        self.assertEqual(result[0][1].rgba, b"\xff\0\0\xff")
        self.assertEqual(result[1][1].rgba, b"\0\xff\0\xff")

    def test_reports_structured_progress(self):
        service = _service(workers=1)
        requests = [
            ("first", self.dds, 0, 0, 1, 1),
            ("second", self.dds, 1, 0, 1, 1),
        ]
        calls = []

        service.load(
            "catalog",
            requests,
            progress=lambda done, total, message: calls.append(
                (done, total, message)
            ),
        )

        self.assertEqual(calls[-1], (2, 2, "Иконки предметов"))
        self.assertTrue(all(total == 2 for _done, total, _msg in calls))

    def test_memory_cache_avoids_second_decode(self):
        service = _service(workers=1)
        requests = [("first", self.dds, 0, 0, 1, 1)]

        service.load("catalog", requests)
        hits_before = service._memory.hits
        service.load("catalog", requests)

        self.assertGreater(service._memory.hits, hits_before)

    def test_disk_bundle_is_reused_by_new_service(self):
        cache_dir = self.root / "cache"
        requests = [
            ("first", self.dds, 0, 0, 1, 1),
            ("second", self.dds, 1, 0, 1, 1),
        ]

        first = _service(cache_dir=cache_dir, workers=1)
        first.load("catalog", requests)

        second = _service(cache_dir=cache_dir, workers=1)
        result = second.load("catalog", requests)

        self.assertEqual(len(result), 2)
        self.assertGreaterEqual(second._bundles.disk_hits, 1)


    def test_region_cache_uses_injected_cache_service(self):
        cache = CacheService()
        service = _service(cache_service=cache, workers=1)

        first = service.read_region(self.dds, 0, 0, 1, 1)
        second = service.read_region(self.dds, 0, 0, 1, 1)

        self.assertEqual(first.rgba, second.rgba)
        stats = cache.stats()["namespaces"]["item.icons.region"]
        self.assertEqual(stats["computes"], 1)
        self.assertEqual(stats["hits"], 1)

    def test_bundle_cache_is_single_flight_and_counted(self):
        cache = CacheService()
        service = _service(cache_service=cache, workers=1)
        requests = [
            ("first", self.dds, 0, 0, 1, 1),
            ("second", self.dds, 1, 0, 1, 1),
        ]

        service.load("catalog", requests)
        service.clear_memory()
        service.load("catalog", requests)

        stats = cache.stats()["namespaces"]["item.icons.bundle"]
        self.assertEqual(stats["computes"], 1)
        self.assertEqual(stats["hits"], 1)

    def test_bundle_hit_loads_regions_added_to_request(self):
        cache = CacheService()
        service = _service(cache_service=cache, workers=1)
        first = [
            ("first", self.dds, 0, 0, 1, 1),
        ]
        both = [
            ("first", self.dds, 0, 0, 1, 1),
            ("second", self.dds, 1, 0, 1, 1),
        ]

        self.assertEqual(
            [section for section, _ in service.load("catalog", first)],
            ["first"],
        )
        result = service.load("catalog", both)

        self.assertEqual(
            [section for section, _ in result],
            ["first", "second"],
        )
        self.assertEqual(result[1][1].rgba, b"\0\xff\0\xff")

    def test_non_dict_bundle_is_ignored(self):
        cache = CacheService()
        service = _service(cache_service=cache, workers=1)
        requests = [("first", self.dds, 0, 0, 1, 1)]
        bundle_key = service._bundle_key("catalog", requests, {})
        cache.set(
            ITEM_ICON_BUNDLE_NAMESPACE,
            bundle_key,
            "broken",
            snapshot_id=service._key_snapshot(bundle_key),
        )

        result = service.load("catalog", requests)

        self.assertEqual(
            [section for section, _ in result],
            ["first"],
        )


if __name__ == "__main__":
    unittest.main()
