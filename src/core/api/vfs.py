# -*- coding: utf-8 -*-
"""Порт файловой системы и VFS."""

from core.tools.cache import file_index_signature
from core.tools.fs import (
    game_root_from_scan_dir,
    normalize_path,
    read_text_safe,
    read_xml_text,
)
from core.tools.vfs import resolve_asset_path

__all__ = [
    "file_index_signature",
    "game_root_from_scan_dir",
    "normalize_path",
    "read_text_safe",
    "read_xml_text",
    "resolve_asset_path",
]
