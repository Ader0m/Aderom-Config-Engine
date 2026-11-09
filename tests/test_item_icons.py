# -*- coding: utf-8 -*-
"""Тесты пакетной загрузки иконок и кэша регионов."""

import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.support import SRC  # noqa: F401

from core.items.icons import ItemIconService


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
        service = ItemIconService(workers=1)
        requests = [
            ("first", self.dds, 0, 0, 1, 1),
            ("second", self.dds, 1, 0, 1, 1),
        ]

        result = service.load("catalog", requests)

        self.assertEqual([section for section, _ in result], ["first", "second"])
        self.assertEqual(result[0][1].rgba, b"\xff\0\0\xff")
        self.assertEqual(result[1][1].rgba, b"\0\xff\0\xff")

    def test_memory_cache_avoids_second_decode(self):
        service = ItemIconService(workers=1)
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

        first = ItemIconService(cache_dir=cache_dir, workers=1)
        first.load("catalog", requests)

        second = ItemIconService(cache_dir=cache_dir, workers=1)
        result = second.load("catalog", requests)

        self.assertEqual(len(result), 2)
        self.assertGreaterEqual(second._bundles.disk_hits, 1)


if __name__ == "__main__":
    unittest.main()
