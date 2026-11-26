# -*- coding: utf-8 -*-
"""Построчная карта изменений черновика проекта.

Карта хранит только диапазоны изменённых строк текущего текста. Она не
заменяет исходный и текущий тексты: это производные данные для UI и
будущих оптимизаций патч-конвейера.

Диапазоны — полуинтервалы ``(start, end)`` с нумерацией строк от 0.
"""

from __future__ import annotations

import difflib
from typing import Iterable, Optional, Tuple

LINE_CHANGE_MAP_VERSION = 1


def _split_lines(text) -> list:
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n").split(
        "\n"
    )


def changed_line_numbers(
    original_text,
    current_text,
    max_lines: Optional[int] = None,
    max_middle: Optional[int] = None,
) -> Optional[set]:
    """Вернуть 0-based индексы изменённых строк или ``None`` при лимите.

    ``None`` означает «расчёт не выполнен из-за лимита», а не «изменений
    нет». Пустое множество означает отсутствие различий.
    """
    old_text = str(original_text or "")
    new_text = str(current_text or "")
    if old_text == new_text:
        return set()
    if max_lines is not None and (
        max(old_text.count("\n"), new_text.count("\n")) + 1 > max_lines
    ):
        return None

    old_lines = _split_lines(old_text)
    new_lines = _split_lines(new_text)

    limit = min(len(old_lines), len(new_lines))
    prefix = 0
    while prefix < limit and old_lines[prefix] == new_lines[prefix]:
        prefix += 1

    suffix = 0
    max_suffix = limit - prefix
    while (
        suffix < max_suffix
        and old_lines[-1 - suffix] == new_lines[-1 - suffix]
    ):
        suffix += 1

    old_end = len(old_lines) - suffix if suffix else len(old_lines)
    new_end = len(new_lines) - suffix if suffix else len(new_lines)
    middle_old = old_lines[prefix:old_end]
    middle_new = new_lines[prefix:new_end]
    if max_middle is not None and (
        max(len(middle_old), len(middle_new)) > max_middle
    ):
        return None

    changed = set()
    matcher = difflib.SequenceMatcher(
        None,
        middle_old,
        middle_new,
        autojunk=False,
    )
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete":
            line = prefix + j1
            if line >= len(new_lines):
                line = len(new_lines) - 1
            if line >= 0:
                changed.add(line)
            continue
        changed.update(range(prefix + j1, prefix + j2))
    return changed


def line_numbers_to_ranges(
    lines: Iterable[int],
) -> Tuple[Tuple[int, int], ...]:
    """Схлопнуть номера строк в полуинтервалы без пересечений."""
    values = sorted({int(line) for line in lines or () if int(line) >= 0})
    if not values:
        return ()
    ranges = []
    start = previous = values[0]
    for line in values[1:]:
        if line == previous + 1:
            previous = line
            continue
        ranges.append((start, previous + 1))
        start = previous = line
    ranges.append((start, previous + 1))
    return tuple(ranges)


def changed_line_ranges(
    original_text,
    current_text,
    max_lines: Optional[int] = None,
    max_middle: Optional[int] = None,
):
    """Диапазоны изменённых строк или ``None`` при превышении лимита."""
    lines = changed_line_numbers(
        original_text,
        current_text,
        max_lines=max_lines,
        max_middle=max_middle,
    )
    if lines is None:
        return None
    return line_numbers_to_ranges(lines)


def serialize_change_map(ranges, revision) -> Optional[dict]:
    """Компактное JSON-представление карты изменений."""
    if ranges is None:
        return None
    return {
        "version": LINE_CHANGE_MAP_VERSION,
        "revision": int(revision or 0),
        "ranges": [[int(start), int(end)] for start, end in ranges],
    }


def deserialize_change_map(value, revision):
    """Прочитать карту, только если она относится к той же ревизии."""
    if not isinstance(value, dict):
        return None
    try:
        version = int(value.get("version") or 0)
        mapped_revision = int(value.get("revision") or 0)
    except (TypeError, ValueError):
        return None
    if version != LINE_CHANGE_MAP_VERSION:
        return None
    if mapped_revision != int(revision or 0):
        return None

    result = []
    for item in value.get("ranges") or ():
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return None
        try:
            start, end = int(item[0]), int(item[1])
        except (TypeError, ValueError):
            return None
        if start < 0 or end <= start:
            return None
        result.append((start, end))
    return tuple(result)


def expand_change_ranges(ranges, line_count=None) -> set:
    """Развернуть диапазоны в номера строк для Scintilla-маркеров."""
    lines = set()
    for start, end in ranges or ():
        for line in range(int(start), int(end)):
            if line_count is not None and line >= int(line_count):
                break
            if line >= 0:
                lines.add(line)
    return lines


__all__ = [
    "LINE_CHANGE_MAP_VERSION",
    "changed_line_numbers",
    "changed_line_ranges",
    "deserialize_change_map",
    "expand_change_ranges",
    "line_numbers_to_ranges",
    "serialize_change_map",
]
