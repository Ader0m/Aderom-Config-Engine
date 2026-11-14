# -*- coding: utf-8 -*-
"""Каталог секций и ключей LTX/DLTX из сырых файлов VFS.

Каталог не запускает резолвер и не читает resolved-текст: это индекс
объявлений в исходных ``.ltx`` с учётом overlay проекта. Он нужен
подсказкам, поэтому при переполнении сознательно обрезается.
"""

from __future__ import annotations

import bisect
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

from core.patching.ltx.document import parse_key_line, parse_section_header
from core.tools.cache import path_key, project_overlay_signature
from core.tools.fs import read_text_safe

CATALOG_VERSION = "1"

_MAX_SECTIONS = 50_000
_MAX_KEYS_PER_SECTION = 100
_MAX_TOTAL_KEYS = 2_000_000
_MAX_PARENTS = 32


@dataclass(frozen=True)
class SectionInfo:
    name: str
    kind: str = "base"
    path: Optional[Path] = None
    line: int = 0
    parents: Tuple[str, ...] = ()
    keys: Tuple[str, ...] = ()

    @property
    def is_deleted(self):
        return self.kind == "delete"


@dataclass(frozen=True)
class SectionSet:
    names: Tuple[str, ...]
    by_name: Dict[str, SectionInfo]
    unique_keys: Tuple[str, ...]
    signature: str = ""
    truncated: bool = False

    def find(self, name) -> Optional[SectionInfo]:
        return self.by_name.get(str(name or "").strip().lower())

    def keys_of(self, name) -> Tuple[str, ...]:
        item = self.find(name)
        return () if item is None else item.keys

    def parents_of(self, name) -> Tuple[str, ...]:
        item = self.find(name)
        return () if item is None else item.parents

    def prefix(self, prefix, limit=100) -> Tuple[SectionInfo, ...]:
        prefix = str(prefix or "").strip().lower()
        start = bisect.bisect_left(self.names, prefix) if prefix else 0
        result = []
        for index in range(start, len(self.names)):
            name = self.names[index]
            if prefix and not name.startswith(prefix):
                break
            item = self.by_name.get(name)
            if item is not None:
                result.append(item)
            if len(result) >= int(limit):
                break
        return tuple(result)

    def __len__(self):
        return len(self.names)


EMPTY_SECTIONS = SectionSet(names=(), by_name={}, unique_keys=())


def _overlay_text_map(overlay) -> dict:
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
            result[path_key(path)] = str(text or "")
        except (TypeError, ValueError, OSError):
            continue
    return result


def _merge_info(current: SectionInfo, incoming: SectionInfo) -> SectionInfo:
    """Слить повторное объявление: путь/вид первого, ключи — все."""
    kind = current.kind
    path = current.path
    line = current.line
    if kind == "delete" and incoming.kind != "delete":
        kind = incoming.kind
        path = incoming.path
        line = incoming.line
    keys = current.keys
    if incoming.keys:
        seen = set(keys)
        extra = [key for key in incoming.keys if key not in seen]
        if extra:
            keys = tuple(list(keys) + extra)[:_MAX_KEYS_PER_SECTION]
    parents = current.parents
    if incoming.parents:
        seen = set(parents)
        extra = [name for name in incoming.parents if name not in seen]
        if extra:
            parents = tuple(list(parents) + extra)[:_MAX_PARENTS]
    return replace(
        current,
        kind=kind,
        path=path,
        line=line,
        keys=keys,
        parents=parents,
    )


def _scan_text(path, text: str) -> Tuple[Dict[str, SectionInfo], set]:
    sections: Dict[str, SectionInfo] = {}
    current = None
    current_deleted = False
    for number, raw_line in enumerate(str(text or "").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(";") or line.startswith("//"):
            continue
        if line.startswith("#"):
            continue

        header = parse_section_header(line)
        if header is not None:
            kind, name, parents, _rest = header
            if not name:
                current = None
                current_deleted = False
                continue
            name = name.lower()
            normalized_parents = []
            for parent in parents:
                parent = str(parent or "").strip().lstrip("!").strip().lower()
                if parent:
                    normalized_parents.append(parent)
            info = SectionInfo(
                name=name,
                kind=kind,
                path=Path(path),
                line=number,
                parents=tuple(normalized_parents[:_MAX_PARENTS]),
            )
            existing = sections.get(name)
            sections[name] = info if existing is None else _merge_info(
                existing,
                info,
            )
            current = name
            current_deleted = kind == "delete"
            continue

        if not current or current_deleted:
            continue
        parsed = parse_key_line(line)
        if not parsed:
            continue
        _operation, key, _value, _has_value = parsed
        key = str(key or "").strip()
        if not key:
            continue
        info = sections.get(current)
        if info is None:
            continue
        if key not in info.keys and len(info.keys) < _MAX_KEYS_PER_SECTION:
            sections[current] = replace(
                info,
                keys=tuple(list(info.keys) + [key]),
            )
    return sections, set(sections)


class SectionCatalog:
    """Сборщик и кэш снимка секций."""

    def __init__(self):
        self._key = None
        self._value = EMPTY_SECTIONS

    def build(
        self,
        forest,
        overlay=None,
        deadline=None,
        max_sections=_MAX_SECTIONS,
    ) -> SectionSet:
        if forest is None:
            return EMPTY_SECTIONS

        base = getattr(forest, "scan_dir", None)
        overlay_texts = _overlay_text_map(overlay)
        overlay_signature = project_overlay_signature(overlay)

        files = []
        seen_paths = set()
        for path in list(getattr(forest, "files", ()) or ()):
            path = Path(path)
            if path.suffix.lower() != ".ltx":
                continue
            files.append(path)
            seen_paths.add(path_key(path))
        for raw_path in list(getattr(overlay, "source_files", {}) or {}):
            path = Path(raw_path)
            if path.suffix.lower() != ".ltx":
                continue
            key = path_key(path)
            if key in seen_paths:
                continue
            seen_paths.add(key)
            files.append(path)

        key = (
            CATALOG_VERSION,
            str(getattr(forest, "cache_id", "") or ""),
            overlay_signature,
            str(base or "").lower(),
            len(files),
        )
        if key == self._key:
            return self._value

        merged: Dict[str, SectionInfo] = {}
        unique_keys = set()
        truncated = False

        for path in files:
            if deadline is not None and time.monotonic() >= deadline:
                truncated = True
                break
            text = overlay_texts.get(path_key(path))
            if text is None:
                try:
                    text = read_text_safe(path)
                except Exception:
                    continue
            found, _names = _scan_text(path, text)
            for name, info in found.items():
                existing = merged.get(name)
                merged[name] = info if existing is None else _merge_info(
                    existing,
                    info,
                )
            for info in found.values():
                for key_name in info.keys:
                    if len(unique_keys) >= _MAX_TOTAL_KEYS:
                        truncated = True
                        break
                    unique_keys.add(key_name)
            if len(merged) >= int(max_sections):
                truncated = True
                break

        names = tuple(sorted(merged))
        value = SectionSet(
            names=names,
            by_name=merged,
            unique_keys=tuple(sorted(unique_keys)),
            signature=str(key),
            truncated=truncated,
        )
        if not truncated:
            self._key = key
            self._value = value
        return value

    def clear(self):
        self._key = None
        self._value = EMPTY_SECTIONS
