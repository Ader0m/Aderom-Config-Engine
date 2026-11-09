# -*- coding: utf-8 -*-
"""Минимальный декодер DDS для иконок инвентаря.

Игровые текстуры X-Ray/Anomaly обычно лежат в ``.dds`` и бывают:

- несжатыми 24/32-битными (BGRA/RGBA);
- 8-битными (alpha/luminance, например атласы шрифтов);
- с блочным сжатием DXT1/DXT3/DXT5.

Полный атлас иконок может быть 4096×4096 и больше, поэтому декодер умеет
читать только нужный прямоугольник (ROI): для одной иконки 50×50 это
несколько блоков, а не миллионы пикселей.

Модуль не зависит от Qt и возвращает сырые байты RGBA. Преобразование в
``QImage`` выполняет модуль, который показывает окно.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from core.tools.fs import normalize_path


DDS_MAGIC = b"DDS "
DDS_HEADER_SIZE = 128

DDPF_ALPHAPIXELS = 0x1
DDPF_ALPHA = 0x2
DDPF_FOURCC = 0x4
DDPF_RGB = 0x40
DDPF_LUMINANCE = 0x20000

DDSD_PITCH = 0x8


class DdsError(Exception):
    """Ошибка чтения/декодирования DDS."""


@dataclass(frozen=True)
class DdsInfo:
    width: int
    height: int
    format: str
    bit_count: int = 0
    pitch: int = 0
    masks: Tuple[int, int, int, int] = (0, 0, 0, 0)


@dataclass(frozen=True)
class TextureRegion:
    """Прямоугольник изображения в формате RGBA (по 4 байта на пиксель)."""

    width: int
    height: int
    rgba: bytes


# ---------------------------------------------------------------- заголовок


def read_dds_info(path) -> DdsInfo:
    """Прочитать заголовок DDS без декодирования пикселей."""
    with open(normalize_path(path), "rb") as fp:
        return _read_info(fp)


def _read_info(fp) -> DdsInfo:
    fp.seek(0)
    head = fp.read(DDS_HEADER_SIZE)

    if len(head) < DDS_HEADER_SIZE or head[:4] != DDS_MAGIC:
        raise DdsError("файл не является DDS")

    (
        _size,
        _flags,
        height,
        width,
        pitch,
        _depth,
        _mipmaps,
    ) = struct.unpack_from("<7I", head, 4)

    (
        _pf_size,
        pf_flags,
        fourcc_int,
        bit_count,
        mask_r,
        mask_g,
        mask_b,
        mask_a,
    ) = struct.unpack_from("<8I", head, 76)

    fourcc = struct.pack("<I", fourcc_int)

    if fourcc in (b"DXT1", b"DXT3", b"DXT5"):
        fmt = fourcc.decode("ascii")
    elif fourcc == b"DX10":
        raise DdsError("DX10-заголовок DDS пока не поддерживается")
    elif pf_flags & DDPF_RGB:
        if bit_count not in (24, 32):
            raise DdsError("неподдерживаемый RGB-формат: %d бит" % bit_count)
        fmt = "RGB%d" % bit_count
    elif pf_flags & DDPF_LUMINANCE:
        if bit_count != 8:
            raise DdsError(
                "неподдерживаемый luminance-формат: %d бит" % bit_count
            )
        fmt = "L8"
    elif pf_flags & (DDPF_ALPHA | DDPF_ALPHAPIXELS):
        if bit_count not in (0, 8):
            raise DdsError(
                "неподдерживаемый alpha-формат: %d бит" % bit_count
            )
        fmt = "A8"
    else:
        raise DdsError(
            "неподдерживаемый DDS: flags=0x%X, fourcc=%r"
            % (pf_flags, fourcc)
        )

    if width <= 0 or height <= 0:
        raise DdsError("некорректный размер DDS: %d×%d" % (width, height))

    return DdsInfo(
        width=int(width),
        height=int(height),
        format=fmt,
        bit_count=int(bit_count),
        pitch=int(pitch or 0),
        masks=(int(mask_r), int(mask_g), int(mask_b), int(mask_a)),
    )


# ---------------------------------------------------------------- публичный вход


def read_dds_region(path, x, y, width, height) -> TextureRegion:
    """Декодировать прямоугольник ``(x, y, width, height)`` из DDS.

    Координаты и размеры — в пикселях. Область обрезается по границам
    изображения. Возвращает RGBA-байты в порядке R, G, B, A.
    """
    try:
        x = int(x)
        y = int(y)
        width = int(width)
        height = int(height)
    except (TypeError, ValueError):
        raise DdsError("некорректный прямоугольник иконки")

    if width <= 0 or height <= 0:
        raise DdsError("некорректный размер иконки: %d×%d" % (width, height))

    path = normalize_path(path)

    with open(path, "rb") as fp:
        info = _read_info(fp)

        x0 = max(0, x)
        y0 = max(0, y)
        x1 = min(info.width, x + width)
        y1 = min(info.height, y + height)

        if x1 <= x0 or y1 <= y0:
            raise DdsError(
                "область иконки вне текстуры: (%d,%d %d×%d), текстура %d×%d"
                % (x, y, width, height, info.width, info.height)
            )

        w = x1 - x0
        h = y1 - y0

        if info.format in ("DXT1", "DXT3", "DXT5"):
            rgba = _decode_dxt_region(fp, info, x0, y0, w, h)
        else:
            rgba = _decode_raw_region(fp, info, x0, y0, w, h)

    return TextureRegion(width=w, height=h, rgba=rgba)


# ---------------------------------------------------------------- несжатые


def _mask_info(mask: int) -> Optional[Tuple[int, int]]:
    if not mask:
        return None
    shift = (mask & -mask).bit_length() - 1
    max_value = mask >> shift
    if max_value <= 0:
        return None
    return shift, max_value


def _extract(value: int, mask_info: Optional[Tuple[int, int]]) -> int:
    if mask_info is None:
        return 0
    shift, max_value = mask_info
    return ((value >> shift) & max_value) * 255 // max_value


def _decode_raw_region(fp, info: DdsInfo, x0, y0, w, h) -> bytes:
    bpp = max(1, info.bit_count // 8)
    packed_row = info.width * bpp
    pitch = info.pitch if info.pitch >= packed_row else packed_row
    row_size = w * bpp

    mask_r, mask_g, mask_b, mask_a = info.masks
    info_r = _mask_info(mask_r)
    info_g = _mask_info(mask_g)
    info_b = _mask_info(mask_b)
    info_a = _mask_info(mask_a)

    out = bytearray(w * h * 4)

    # 8-битные alpha-атласы (шрифты) не содержат RGB — показываем их
    # белым силуэтом с исходной прозрачностью.
    alpha_only = info.format == "A8"
    luminance = info.format == "L8"

    read_pixel = None
    if bpp == 4:
        read_pixel = lambda data, offset: struct.unpack_from(
            "<I", data, offset
        )[0]
    elif bpp == 3:
        read_pixel = lambda data, offset: (
            data[offset]
            | (data[offset + 1] << 8)
            | (data[offset + 2] << 16)
        )
    else:
        read_pixel = lambda data, offset: data[offset]

    for row in range(h):
        fp.seek(DDS_HEADER_SIZE + (y0 + row) * pitch + x0 * bpp)
        data = fp.read(row_size)
        if len(data) < row_size:
            data += b"\x00" * (row_size - len(data))

        dst_row = row * w * 4
        for col in range(w):
            value = read_pixel(data, col * bpp)

            if alpha_only:
                r = g = b = 255
                a = _extract(value, info_a) or (
                    value & 0xFF if bpp == 1 else 255
                )
            elif luminance:
                mask = info_r or info_g or info_b
                lum = (
                    _extract(value, mask) if mask is not None
                    else (value & 0xFF if bpp == 1 else 0)
                )
                r = g = b = lum
                a = _extract(value, info_a) if info_a else 255
            else:
                r = _extract(value, info_r)
                g = _extract(value, info_g)
                b = _extract(value, info_b)
                a = _extract(value, info_a) if info_a else 255

            offset = dst_row + col * 4
            out[offset] = r
            out[offset + 1] = g
            out[offset + 2] = b
            out[offset + 3] = a

    return bytes(out)


# ---------------------------------------------------------------- DXT


def _rgb565(value: int) -> Tuple[int, int, int]:
    r = (value >> 11) & 0x1F
    g = (value >> 5) & 0x3F
    b = value & 0x1F
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))


def _dxt_color_pixels(block: bytes, offset: int, four_color: bool):
    c0, c1 = struct.unpack_from("<HH", block, offset)
    r0, g0, b0 = _rgb565(c0)
    r1, g1, b1 = _rgb565(c1)

    colors = [
        (r0, g0, b0, 255),
        (r1, g1, b1, 255),
    ]

    if not four_color and c0 <= c1:
        colors.append(
            ((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2, 255)
        )
        colors.append((0, 0, 0, 0))
    else:
        colors.append(
            (
                (2 * r0 + r1) // 3,
                (2 * g0 + g1) // 3,
                (2 * b0 + b1) // 3,
                255,
            )
        )
        colors.append(
            (
                (r0 + 2 * r1) // 3,
                (g0 + 2 * g1) // 3,
                (b0 + 2 * b1) // 3,
                255,
            )
        )

    bits = struct.unpack_from("<I", block, offset + 4)[0]
    return [colors[(bits >> (2 * i)) & 0x3] for i in range(16)]


def _dxt3_alphas(block: bytes):
    bits = int.from_bytes(block[0:8], "little")
    return [((bits >> (4 * i)) & 0xF) * 17 for i in range(16)]


def _dxt5_alphas(block: bytes):
    a0 = block[0]
    a1 = block[1]

    if a0 > a1:
        palette = [a0, a1] + [
            ((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)
        ]
    else:
        palette = [a0, a1] + [
            ((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)
        ] + [0, 255]

    bits = int.from_bytes(block[2:8], "little")
    return [palette[(bits >> (3 * i)) & 0x7] for i in range(16)]


def _decode_dxt_block(block: bytes, fmt: str) -> bytes:
    out = bytearray(64)

    if fmt == "DXT1":
        pixels = _dxt_color_pixels(block, 0, four_color=False)
        for i, (r, g, b, a) in enumerate(pixels):
            offset = i * 4
            out[offset] = r
            out[offset + 1] = g
            out[offset + 2] = b
            out[offset + 3] = a
        return bytes(out)

    if len(block) < 16:
        block = block + b"\x00" * (16 - len(block))

    if fmt == "DXT3":
        alphas = _dxt3_alphas(block)
    elif fmt == "DXT5":
        alphas = _dxt5_alphas(block)
    else:
        alphas = [255] * 16

    pixels = _dxt_color_pixels(block, 8, four_color=True)
    for i, (r, g, b, _a) in enumerate(pixels):
        offset = i * 4
        out[offset] = r
        out[offset + 1] = g
        out[offset + 2] = b
        out[offset + 3] = alphas[i]

    return bytes(out)


def _decode_dxt_region(fp, info: DdsInfo, x0, y0, w, h) -> bytes:
    block_size = 8 if info.format == "DXT1" else 16
    blocks_per_row = (info.width + 3) // 4

    bx0 = x0 // 4
    by0 = y0 // 4
    bx1 = (x0 + w + 3) // 4
    by1 = (y0 + h + 3) // 4

    tmp_w = (bx1 - bx0) * 4
    tmp_h = (by1 - by0) * 4
    tmp = bytearray(tmp_w * tmp_h * 4)

    for by in range(by0, by1):
        fp.seek(
            DDS_HEADER_SIZE
            + (by * blocks_per_row + bx0) * block_size
        )
        row = fp.read((bx1 - bx0) * block_size)
        if len(row) < (bx1 - bx0) * block_size:
            row += b"\x00" * ((bx1 - bx0) * block_size - len(row))

        for bx in range(bx0, bx1):
            offset = (bx - bx0) * block_size
            block = row[offset:offset + block_size]
            pixels = _decode_dxt_block(block, info.format)

            local_x = (bx - bx0) * 4
            local_y = (by - by0) * 4

            for py in range(4):
                dst = ((local_y + py) * tmp_w + local_x) * 4
                src = py * 16
                tmp[dst:dst + 16] = pixels[src:src + 16]

    # Вырезать запрошенный прямоугольник из выровненного по блокам буфера.
    ox = x0 - bx0 * 4
    oy = y0 - by0 * 4
    out = bytearray(w * h * 4)

    for row in range(h):
        src = ((oy + row) * tmp_w + ox) * 4
        dst = row * w * 4
        out[dst:dst + w * 4] = tmp[src:src + w * 4]

    return bytes(out)
