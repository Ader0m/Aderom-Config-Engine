# -*- coding: utf-8 -*-
"""Прикладные DTO для read-only карточки секции.

Модуль не содержит Qt и не является вторым источником истины: он только
переводит семантическую модель ``core.api.resolve`` в простые значения,
которые может показать UI-слой.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from core.api.resolve import (
    Diagnostics,
    SectionSnapshot,
    SourceRef,
    ValueSource,
)


@dataclass(frozen=True, slots=True)
class SectionCardSource:
    """Источник значения или заголовка секции."""

    file: Optional[Path]
    display: str
    mod: Optional[str]
    depth: Optional[int]
    operation: str
    layer: str


@dataclass(frozen=True, slots=True)
class SectionCardProperty:
    """Одна строка таблицы свойств.

    ``value`` ограничен по длине: карточка read-only и не должна держать
    полную копию многомегабайтного значения в UI-слое.
    """

    key: str
    value: Optional[str]
    source: SectionCardSource
    chain: Tuple[SectionCardSource, ...]
    confidence: str


@dataclass(frozen=True, slots=True)
class SectionCardDiagnostic:
    """Диагностика, относящаяся к секции или корню."""

    code: str
    message: str
    severity: str


@dataclass(frozen=True, slots=True)
class SectionCardData:
    """Готовое read-only представление секции."""

    name: str
    kind: str
    parents: Tuple[str, ...]
    declaration: SectionCardSource
    source: SectionCardSource
    properties: Tuple[SectionCardProperty, ...]
    diagnostics: Tuple[SectionCardDiagnostic, ...]


CARD_VALUE_LIMIT = 400


def _card_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    if len(text) <= CARD_VALUE_LIMIT:
        return text
    return "%s… (%d симв.)" % (text[:CARD_VALUE_LIMIT], len(text))


def _source_display(file_path, mod: Optional[str]) -> str:
    if file_path is None:
        return "?"
    name = Path(file_path).name
    if mod:
        return "%s / %s" % (mod, name)
    return name


def _value_source(source: ValueSource) -> SectionCardSource:
    mod_name = source.mod.name if source.mod is not None else None
    file_path = source.file
    return SectionCardSource(
        file=Path(file_path) if file_path is not None else None,
        display=_source_display(file_path, mod_name),
        mod=mod_name,
        depth=source.depth,
        operation=str(source.operation or ""),
        layer=str(source.layer or ""),
    )


def _source_ref(source: SourceRef) -> SectionCardSource:
    file_path = source.file
    mod_name = source.mod.name if source.mod is not None else None
    return SectionCardSource(
        file=Path(file_path) if file_path is not None else None,
        display=_source_display(file_path, mod_name),
        mod=mod_name,
        depth=None,
        operation="section",
        layer="raw",
    )


def build_section_card(section: SectionSnapshot,
                       diagnostics: Optional[Diagnostics] = None
                       ) -> SectionCardData:
    """Построить DTO секции из ``core.api.resolve.SectionSnapshot``."""
    name = str(section.name or "").lower()
    properties = []
    for prop in section.properties:
        winner = prop.winner
        properties.append(
            SectionCardProperty(
                key=str(prop.key or ""),
                value=_card_value(prop.value),
                source=_value_source(winner),
                chain=tuple(_value_source(item) for item in prop.chain),
                confidence=str(prop.confidence or ""),
            )
        )

    section_diagnostics = []
    if diagnostics is not None:
        for item in diagnostics.items:
            if item.section is not None and str(item.section).lower() != name:
                continue
            section_diagnostics.append(
                SectionCardDiagnostic(
                    code=str(item.code or ""),
                    message=str(item.message or ""),
                    severity=str(item.severity or ""),
                )
            )

    return SectionCardData(
        name=name,
        kind=str(section.kind or ""),
        parents=tuple(section.parents),
        declaration=_source_ref(section.declaration),
        source=_source_ref(section.source),
        properties=tuple(properties),
        diagnostics=tuple(section_diagnostics),
    )


__all__ = [
    "SectionCardData",
    "SectionCardDiagnostic",
    "SectionCardProperty",
    "SectionCardSource",
    "build_section_card",
]
