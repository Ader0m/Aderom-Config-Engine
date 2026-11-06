# -*- coding: utf-8 -*-
"""Тесты минимального DDS-декодера для иконок предметов."""

import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.support import SRC  # noqa: F401

from core.dds import DdsError, read_dds_info, read_dds_region


def _header(width, height, *, fourcc=b"\0\0\0\0", pf_flags=0x41,
            bits=32, masks=(0x000000FF, 0x0000FF00, 0x00FF0000, 0xFF000000),
            pitch=0):
    """Собрать 128-байтный заголовок DDS."""
    header = bytearray(128)
    header[0:4] = b"DDS "

    struct.pack_into("<7I", header, 4, 124, 0, height, width, pitch, 0, 1)
    struct.pack_into(
        "<8I",
        header,
        76,
        32,
        pf_flags,
        struct.unpack("<I", fourcc)[0],
        bits,
        masks[0],
        masks[1],
        masks[2],
        masks[3],
    )
    return bytes(header)


class DdsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, data):
        path = self.root / name
        path.write_bytes(data)
        return path

    def test_uncompressed_rgba_region(self):
        # R mask в младшем байте: байты [R, G, B, A].
        pixels = bytes([
            255, 0, 0, 255,       # красный
            0, 255, 0, 255,       # зелёный
            0, 0, 255, 255,       # синий
            255, 255, 255, 255,   # белый
        ])
        path = self._write(
            "rgba.dds",
            _header(2, 2) + pixels,
        )

        info = read_dds_info(path)
        self.assertEqual((info.width, info.height), (2, 2))

        region = read_dds_region(path, 1, 0, 1, 2)
        self.assertEqual((region.width, region.height), (1, 2))
        self.assertEqual(
            region.rgba,
            bytes([0, 255, 0, 255, 255, 255, 255, 255]),
        )

    def test_uncompressed_bgra_masks(self):
        # Как в реальных атласах Anomaly: R=0x00FF0000, B=0x000000FF.
        # Байты [B, G, R, A].
        path = self._write(
            "bgra.dds",
            _header(
                1,
                1,
                masks=(0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000),
            ) + bytes([0, 0, 255, 255]),
        )
        self.assertEqual(read_dds_region(path, 0, 0, 1, 1).rgba, b"\xff\0\0\xff")

    def test_uncompressed_24bit_bgr(self):
        path = self._write(
            "bgr.dds",
            _header(
                1,
                1,
                pf_flags=0x40,
                bits=24,
                masks=(0x00FF0000, 0x0000FF00, 0x000000FF, 0),
            ) + bytes([0, 0, 255]),
        )
        self.assertEqual(read_dds_region(path, 0, 0, 1, 1).rgba, b"\xff\0\0\xff")

    def test_dxt1_red_and_blue(self):
        # c0 = красный (565), c1 = синий. Индексы: 0,1,2,3...
        indices = 0
        for i, value in enumerate([0, 1, 2, 3] * 4):
            indices |= value << (2 * i)

        block = struct.pack("<HHI", 0xF800, 0x001F, indices)
        path = self._write(
            "dxt1.dds",
            _header(4, 4, fourcc=b"DXT1", pf_flags=0x4, bits=0,
                    masks=(0, 0, 0, 0)) + block,
        )

        region = read_dds_region(path, 0, 0, 4, 4)
        self.assertEqual(region.rgba[0:4], b"\xff\0\0\xff")
        self.assertEqual(region.rgba[4:8], b"\0\0\xff\xff")

    def test_dxt5_alpha(self):
        # Альфа: a0=255, a1=0, индексы 0 для первого пикселя.
        alpha = bytes([255, 0, 0, 0, 0, 0, 0, 0])
        # Цвет: c0=c1=белый, индексы 0.
        color = struct.pack("<HHI", 0xFFFF, 0xFFFF, 0)
        path = self._write(
            "dxt5.dds",
            _header(4, 4, fourcc=b"DXT5", pf_flags=0x4, bits=0,
                    masks=(0, 0, 0, 0)) + alpha + color,
        )

        region = read_dds_region(path, 0, 0, 1, 1)
        self.assertEqual(region.rgba, b"\xff\xff\xff\xff")

    def test_region_outside_texture_raises(self):
        path = self._write("small.dds", _header(2, 2) + b"\0" * 16)
        with self.assertRaises(DdsError):
            read_dds_region(path, 10, 10, 4, 4)


if __name__ == "__main__":
    unittest.main()
