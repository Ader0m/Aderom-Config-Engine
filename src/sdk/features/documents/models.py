# -*- coding: utf-8 -*-
"""Документные DTO SDK: текст, resolved-документы, XML-резолв."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol, Sequence


class LineSource(Protocol):
    """Владелец строки итогового текста (см. core.models.LineSource)."""

    kind: str
    display: str
    path: Optional[Path]
    line: int
    open_raw: bool

@dataclass(frozen=True)
class FileView:
    """Представление файла для проекта (например, для предпросмотра).

    ``text`` — итоговый человекочитаемый вид. Для ``.ogg`` это
    комментарий движка; для текстовых форматов — обычный текст.
    ``label`` и ``details`` показываются в панели предпросмотра и не
    влияют на сам патч.
    """

    text: str
    label: str = ""
    details: tuple[str, ...] = field(default_factory=tuple)

@dataclass(frozen=True)
class XmlResolveResult:
    """Результат XML-резолва (include-сборка + DXML)."""

    text: str
    virtual_name: str
    dxml_applied: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)
    line_sources: tuple[LineSource, ...] = field(default_factory=tuple)

@dataclass(frozen=True)
class ResolvedDraftInfo:
    """Черновик resolved-файла активного проекта."""

    text: str
    saved_text: str = ""
    original_text: str = ""
    line_sources: tuple = field(default_factory=tuple)

    @property
    def is_dirty(self) -> bool:
        return self.text != self.saved_text

@dataclass(frozen=True)
class ResolvedLineEdit:
    """Точечная правка одной строки resolved-файла."""

    action: str          # replace | insert | delete
    line_number: int
    text: str = ""

@dataclass(frozen=True)
class ResolvedDocumentChange:
    """Событие изменения живого resolved-документа.

    ``sections`` — секции, которых коснулась правка. Пустой кортеж
    означает «неизвестно, обновите интересующую секцию».
    """

    tab_id: str
    revision: int
    sections: tuple[str, ...] = field(default_factory=tuple)
    full: bool = False
    focus_line: int = 0
