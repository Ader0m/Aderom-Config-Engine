# -*- coding: utf-8 -*-
"""Порт графических примитивов: DDS-текстуры."""

from core.graphics.dds import (
    DdsError,
    DdsInfo,
    TextureRegion,
    read_dds_info,
    read_dds_region,
)

__all__ = [
    "DdsError",
    "DdsInfo",
    "TextureRegion",
    "read_dds_info",
    "read_dds_region",
]
