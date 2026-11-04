# -*- coding: utf-8 -*-
"""Анализ правок resolved-текста.

Resolved-вид показывает итоговые строки, а ``LineSource`` для каждой строки
указывает, какой файл (и какая строка) победил. Этот модуль связывает
изменённую строку с секцией, ключом и победителем.

Патч всё равно привязывается к корню загрузки resolved-файла: DLTX-патч
загружается последним и перекрывает победителя. Источник победителя нужен
для объяснения пользователю, а не для записи в исходный файл.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

from .document_model import (
    parse_key_line,
    parse_section_header,
)
from .fs import strip_comments


@dataclass(frozen=True)
class ResolvedLineChange:
    """Изменение одной строки resolved-текста."""

    line_number: int
    old_line: str
    new_line: str
    section: Optional[str] = None
    key: Optional[str] = None
    source: object = None
    kind: str = "value"      # value | structural | unsupported
    note: str = ""

    @property
    def is_value_change(self) -> bool:
        return (
            self.kind == "value"
            and bool(self.section)
            and bool(self.key)
        )

    @property
    def winner_label(self) -> str:
        source = self.source
        if source is None:
            return "неизвестен"

        display = getattr(source, "display", "") or ""
        if display:
            return display

        path = getattr(source, "path", None)
        if path is not None:
            return str(path)

        return "неизвестен"


@dataclass(frozen=True)
class ResolvedEditAnalysis:
    """Результат анализа правки resolved-текста."""

    changes: Tuple[ResolvedLineChange, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.changes

    @property
    def is_supported(self) -> bool:
        return bool(self.changes) and all(
            change.is_value_change for change in self.changes
        )

    @property
    def unsupported(self) -> Tuple[ResolvedLineChange, ...]:
        return tuple(
            change for change in self.changes
            if not change.is_value_change
        )

    def notes(self) -> Tuple[str, ...]:
        return tuple(
            _format_change(change) for change in self.changes
        )


def _context(
    lines: Sequence[str],
) -> Tuple[List[Optional[str]], List[Optional[str]], List[bool], List[bool]]:
    sections: List[Optional[str]] = []
    keys: List[Optional[str]] = []
    section_lines: List[bool] = []
    key_lines: List[bool] = []

    current_section = None
    current_key = None

    for raw in lines:
        line = strip_comments(raw).strip()
        is_section = False
        is_key = False

        header = parse_section_header(line)
        if header is not None:
            current_section = header[1]
            current_key = None
            is_section = True
        else:
            parsed = parse_key_line(line)
            if parsed is not None:
                current_key = parsed[1]
                is_key = True

        sections.append(current_section)
        keys.append(current_key)
        section_lines.append(is_section)
        key_lines.append(is_key)

    return sections, keys, section_lines, key_lines


def line_context(lines: Sequence[str]):
    """Публичный контекст строк: секции, ключи, признаки строк."""
    return _context(lines)


def _format_change(change: ResolvedLineChange) -> str:
    prefix = "Строка %d" % change.line_number

    if change.is_value_change:
        return "%s: [%s] %s — победитель: %s" % (
            prefix,
            change.section,
            change.key,
            change.winner_label,
        )

    if change.note:
        return "%s: %s" % (prefix, change.note)

    return "%s: изменение структуры resolved-текста не поддержано" % prefix


def analyze_resolved_changes(
    original_text: str,
    edited_text: str,
    line_sources: Iterable = (),
) -> ResolvedEditAnalysis:
    """Сравнить baseline и правку resolved-текста по строкам."""
    old_lines = str(original_text or "").replace("\r\n", "\n").split("\n")
    new_lines = str(edited_text or "").replace("\r\n", "\n").split("\n")
    sources = list(line_sources or ())

    sections, keys, section_lines, key_lines = _context(old_lines)

    changes: List[ResolvedLineChange] = []
    matcher = difflib.SequenceMatcher(
        None,
        old_lines,
        new_lines,
        autojunk=False,
    )

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue

        if tag != "replace" or (i2 - i1) != (j2 - j1):
            # Вставка/удаление строк меняет структуру resolved-текста.
            if tag == "insert" or (tag == "replace" and i2 == i1):
                changes.append(
                    ResolvedLineChange(
                        line_number=i1 + 1,
                        old_line="",
                        new_line="\n".join(new_lines[j1:j2]),
                        section=sections[i1] if i1 < len(sections) else None,
                        key=keys[i1] if i1 < len(keys) else None,
                        kind="unsupported",
                        note=(
                            "вставка строк в resolved-виде не поддержана; "
                            "измените значение существующей строки"
                        ),
                    )
                )
                continue

            for i in range(i1, i2):
                changes.append(
                    ResolvedLineChange(
                        line_number=i + 1,
                        old_line=old_lines[i],
                        new_line="",
                        section=sections[i] if i < len(sections) else None,
                        key=keys[i] if i < len(keys) else None,
                        source=(
                            sources[i] if i < len(sources) else None
                        ),
                        kind="unsupported",
                        note=(
                            "вставка или удаление строк в resolved-виде "
                            "не поддержана; измените значение строки"
                        ),
                    )
                )
            continue

        for offset in range(i2 - i1):
            i = i1 + offset
            j = j1 + offset
            old_line = old_lines[i]
            new_line = new_lines[j]

            old_clean = strip_comments(old_line).strip()
            new_clean = strip_comments(new_line).strip()

            source = sources[i] if i < len(sources) else None
            section = sections[i] if i < len(sections) else None
            key = keys[i] if i < len(keys) else None

            new_header = parse_section_header(new_clean)
            new_key = parse_key_line(new_clean)

            if (
                (i < len(section_lines) and section_lines[i])
                or new_header is not None
            ):
                kind = "structural"
                note = "изменение заголовка секции не поддержано"
            elif (
                i < len(key_lines)
                and key_lines[i]
                and new_key is not None
                and new_key[1] == key
                and section
            ):
                kind = "value"
                note = ""
            else:
                kind = "unsupported"
                note = "изменение не является заменой значения ключа"

            changes.append(
                ResolvedLineChange(
                    line_number=i + 1,
                    old_line=old_line,
                    new_line=new_line,
                    section=section,
                    key=key,
                    source=source,
                    kind=kind,
                    note=note,
                )
            )

    return ResolvedEditAnalysis(changes=tuple(changes))
