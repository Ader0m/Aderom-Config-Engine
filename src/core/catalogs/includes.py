# -*- coding: utf-8 -*-
"""Каталог путей VFS для подсказок ``#include``.

Пути хранятся относительно каталога конфигов и всегда с ``/``. Каталог
не читает содержимое файлов: только пути из леса, overlay и новых файлов
проекта.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Tuple

CATALOG_VERSION = "1"


@dataclass(frozen=True)
class IncludeEntry:
    rel_path: str
    is_dir: bool
    suffix: str = ""


@dataclass(frozen=True)
class IncludeSet:
    entries: Tuple[IncludeEntry, ...]
    signature: str

    def prefix(self, prefix, limit=100, suffix=None):
        prefix = str(prefix or "").replace("\\", "/").lower()
        wanted_suffix = None
        if suffix:
            wanted_suffix = str(suffix).lower()
        result = []
        for entry in self.entries:
            if wanted_suffix and not entry.is_dir:
                if entry.suffix != wanted_suffix:
                    continue
            if prefix and not entry.rel_path.lower().startswith(prefix):
                continue
            result.append(entry)
            if len(result) >= int(limit):
                break
        return tuple(result)


EMPTY_INCLUDES = IncludeSet(entries=(), signature="empty")


def _norm_rel(value) -> Optional[str]:
    text = str(value or "").replace("\\", "/").strip()
    if not text:
        return None
    text = text.lstrip("./")
    if not text or text.startswith("../") or text == "..":
        return None
    return text


def _rel_from(base: Path, path: Path) -> Optional[str]:
    try:
        value = os.path.relpath(str(path), str(base))
    except (TypeError, ValueError, OSError):
        return None
    return _norm_rel(value)


def _overlay_paths(overlay) -> Iterable:
    if not overlay:
        return ()
    source_files = getattr(overlay, "source_files", None)
    if source_files is not None:
        try:
            for path in source_files:
                yield path, True
        except TypeError:
            pass
    new_files = getattr(overlay, "new_files", None) or ()
    for path in new_files:
        yield path, True


class IncludeCatalog:
    def __init__(self):
        self._key = None
        self._value = EMPTY_INCLUDES

    @staticmethod
    def _base(forest) -> Optional[Path]:
        value = getattr(forest, "scan_dir", None)
        return Path(value) if value else None

    def load(self, forest, overlay=None) -> IncludeSet:
        base = self._base(forest)
        if base is None:
            return EMPTY_INCLUDES

        paths = {}
        for path in list(getattr(forest, "files", ()) or ()):
            rel = _rel_from(base, Path(path))
            if rel:
                paths[rel] = False
        for path in list(getattr(forest, "xml_files", ()) or ()):
            rel = _rel_from(base, Path(path))
            if rel:
                paths[rel] = False
        for path, _is_overlay in _overlay_paths(overlay):
            rel = _rel_from(base, Path(path))
            if rel:
                paths[rel] = False

        directories = set()
        for rel in paths:
            parts = rel.split("/")
            for index in range(1, len(parts)):
                directories.add("/".join(parts[:index]))

        entries = []
        for rel in sorted(directories, key=str.lower):
            entries.append(IncludeEntry(rel_path=rel + "/", is_dir=True))
        file_entries = []
        for rel in paths:
            suffix = Path(rel).suffix.lower()
            file_entries.append(
                IncludeEntry(rel_path=rel, is_dir=False, suffix=suffix)
            )
        file_entries.sort(key=lambda item: item.rel_path.lower())
        entries.extend(file_entries)

        key = (
            CATALOG_VERSION,
            str(getattr(forest, "cache_id", "") or ""),
            str(base).lower(),
            hash(tuple(sorted(paths))),
        )
        if key == self._key:
            return self._value
        value = IncludeSet(entries=tuple(entries), signature=str(key))
        self._key = key
        self._value = value
        return value

    def clear(self):
        self._key = None
        self._value = EMPTY_INCLUDES
