# -*- coding: utf-8 -*-
"""Каталог цветов ``configs/ui/color_defs.xml``.

Данные читаются с учётом overlay проекта и кэшируются по снимку леса,
подписи overlay и штампу файла. Модуль не знает о UI: он отдаёт
нормализованные ``(r, g, b, a)``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple

from core.tools.cache import path_key
from core.tools.fs import read_text_safe

Color = Tuple[int, int, int, int]

_COLOR_DEF_RE = re.compile(r"<color\b([^>]*)>", re.IGNORECASE)
_ATTR_RE = re.compile(r"""([\w:.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
CATALOG_VERSION = "1"


@dataclass(frozen=True)
class ColorDef:
    name: str
    rgba: Color
    path: Optional[Path] = None
    line: int = 0


@dataclass(frozen=True)
class ColorSet:
    """Неизменяемый снимок цветов."""

    by_name: Mapping[str, ColorDef]
    entries: Tuple[ColorDef, ...]
    signature: str

    def get(self, name):
        return self.by_name.get(str(name or "").strip().lower())

    def rgba(self, name) -> Optional[Color]:
        item = self.get(name)
        return None if item is None else item.rgba

    def __len__(self):
        return len(self.entries)


EMPTY_COLORS = ColorSet(by_name={}, entries=(), signature="empty")


def _byte(value) -> int:
    try:
        return int(str(value).strip() or 0) & 0xFF
    except (TypeError, ValueError):
        return 0


def parse_color_definitions(text) -> Dict[str, Color]:
    """Разобрать ``color_defs.xml`` по правилам движка."""
    result: Dict[str, Color] = {}
    for match in _COLOR_DEF_RE.finditer(str(text or "")):
        attrs = {}
        for attr in _ATTR_RE.finditer(match.group(1)):
            key = attr.group(1).lower()
            value = attr.group(2)
            if value is None:
                value = attr.group(3)
            attrs[key] = value

        name = str(attrs.get("name", "")).strip()
        if not name:
            continue
        result[name.lower()] = (
            _byte(attrs.get("r", 0)),
            _byte(attrs.get("g", 0)),
            _byte(attrs.get("b", 0)),
            _byte(attrs.get("a", 255)),
        )
    return result


def _overlay_files(overlay) -> dict:
    if not overlay:
        return {}
    source_files = getattr(overlay, "source_files", None)
    if source_files is None:
        source_files = overlay
    try:
        items = source_files.items()
    except AttributeError:
        return {}
    result = {}
    for path, text in items:
        try:
            result[path_key(path)] = (Path(path), str(text or ""))
        except (TypeError, ValueError, OSError):
            continue
    return result


def _overlay_text(overlay_files, path) -> Optional[str]:
    item = overlay_files.get(path_key(path))
    return None if item is None else item[1]


def _file_stamp(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError:
        return "missing"
    return "%d:%d" % (stat.st_mtime_ns, stat.st_size)


class ColorCatalog:
    """Ленивый кэш цветов по снимку проекта."""

    def __init__(self):
        self._key = None
        self._value = EMPTY_COLORS

    @staticmethod
    def _config_dir(forest, config_dir=None) -> Optional[Path]:
        value = config_dir
        if value is None and forest is not None:
            value = getattr(forest, "scan_dir", None)
        return Path(value) if value else None

    def load(
        self,
        forest,
        overlay=None,
        config_dir=None,
    ) -> ColorSet:
        cfg = self._config_dir(forest, config_dir)
        if cfg is None:
            return EMPTY_COLORS

        path = cfg / "ui" / "color_defs.xml"
        overlay_files = _overlay_files(overlay)
        text = _overlay_text(overlay_files, path)
        if text is not None:
            overlay_key = "overlay:%d" % hash(text)
        else:
            overlay_key = "file:%s" % _file_stamp(path)
        key = (
            CATALOG_VERSION,
            str(getattr(forest, "cache_id", "") or ""),
            str(path).lower(),
            overlay_key,
        )
        if key == self._key:
            return self._value

        if text is None:
            try:
                text = read_text_safe(path)
            except Exception:
                text = ""

        colors = parse_color_definitions(text)
        entries = []
        for name, rgba in colors.items():
            entries.append(ColorDef(name=name, rgba=rgba, path=path))
        entries.sort(key=lambda item: item.name)
        value = ColorSet(
            by_name={item.name: item for item in entries},
            entries=tuple(entries),
            signature=str(key),
        )
        self._key = key
        self._value = value
        return value

    def clear(self):
        self._key = None
        self._value = EMPTY_COLORS
