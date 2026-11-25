# -*- coding: utf-8 -*-
"""Воспроизводимый дамп секций и сравнение семантического паритета.

Формат дампа стабилен и не зависит от UI:

    section<TAB>key_count

Для сравнения с игрой достаточно поимённого списка секций; если в дампе
есть только имена, сравниваются множества, а не количества ключей.
Известные отличия задаются отдельным JSON-реестром.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


SUMMARY_HEADER = "section\tkey_count"
PROPERTY_HEADER = (
    "section\tkey\tvalue\tfile\tdepth\toperation\tconfidence"
)


def dump_section_summary(index) -> str:
    """Стабильный дамп ``section<TAB>keys`` по всем секциям модели."""
    lines = [SUMMARY_HEADER]
    counts = index.key_counts()
    for name in index.names:
        lines.append("%s\t%d" % (name, counts.get(name, 0)))
    return "\n".join(lines) + "\n"


def parse_section_dump(text: str) -> dict:
    """Разобрать поимённый дамп или property-дамп в ``name -> count|None``.

    Поддерживаются:
    - ``name<TAB>count``;
    - ``name`` по одной строке;
    - property-дамп с заголовком ``section<TAB>key<TAB>...``.
    Комментарии ``#``/``;`` и пустые строки игнорируются.
    """
    lines = text.splitlines()
    header = lines[0].split("\t") if lines else []
    property_mode = bool(
        len(header) > 1
        and header[0].strip().lower() == "section"
        and header[1].strip().lower() == "key"
    )
    counts = {}
    properties = {}

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            continue
        if stripped.lower().startswith("section\t"):
            continue

        parts = stripped.split("\t")
        name = str(parts[0]).strip().lower()
        if not name:
            continue

        if property_mode:
            properties.setdefault(name, 0)
            properties[name] += 1
            continue

        if len(parts) > 1:
            try:
                counts[name] = int(parts[1].strip())
            except (TypeError, ValueError):
                counts[name] = None
        else:
            counts.setdefault(name, None)

    if property_mode:
        return properties
    return counts


def parse_manifest(path) -> dict:
    """Прочитать ``stage0-manifest.json`` как ``name -> count``."""
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    sections = data.get("sections")
    if not isinstance(sections, dict):
        raise ValueError("Манифест не содержит словарь sections")
    return {
        str(name).lower(): int(count)
        for name, count in sections.items()
    }


def load_known_differences(path) -> set:
    """Прочитать реестр известных отличий (JSON)."""
    if path is None:
        return set()
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    entries = data.get("entries", data if isinstance(data, list) else [])
    known = set()
    for entry in entries or ():
        if isinstance(entry, str):
            name = entry
        else:
            name = entry.get("section")
        if name:
            known.add(str(name).lower())
    return known


@dataclass(frozen=True, slots=True)
class ParityResult:
    resolver_total: int
    game_total: int
    only_resolver: tuple
    only_game: tuple
    count_differences: tuple
    known_ignored: tuple

    @property
    def ok(self) -> bool:
        return (
            not self.only_resolver
            and not self.only_game
            and not self.count_differences
        )

    def as_text(self) -> str:
        lines = [
            "resolver_sections\t%d" % self.resolver_total,
            "game_sections\t%d" % self.game_total,
            "only_resolver\t%d" % len(self.only_resolver),
            "only_game\t%d" % len(self.only_game),
            "count_differences\t%d" % len(self.count_differences),
            "known_ignored\t%d" % len(self.known_ignored),
        ]
        for name in self.only_resolver:
            lines.append("only_resolver\t%s" % name)
        for name in self.only_game:
            lines.append("only_game\t%s" % name)
        for name, left, right in self.count_differences:
            lines.append(
                "count_difference\t%s\t%s\t%s"
                % (name, "" if left is None else left, right)
            )
        for name in self.known_ignored:
            lines.append("known_ignored\t%s" % name)
        return "\n".join(lines) + "\n"


def compare_section_dumps(resolver, game,
                          known: Optional[Iterable[str]] = None
                          ) -> ParityResult:
    """Сравнить два ``name -> count|None`` словаря.

    ``known`` — реестр секций, отличия которых уже приняты. Для такой
    секции не репортятся ни отсутствие на одной стороне, ни разница
    количества ключей; если отличие действительно было, имя попадает
    в ``known_ignored``. Секция из ``known`` без отличий в
    ``known_ignored`` не попадает.
    """
    known = {str(name).lower() for name in (known or ())}
    resolver_names = set(resolver)
    game_names = set(game)

    only_resolver = tuple(
        sorted(name for name in resolver_names - game_names
               if name not in known)
    )
    only_game = tuple(
        sorted(name for name in game_names - resolver_names
               if name not in known)
    )
    known_ignored = set(
        (resolver_names ^ game_names) & known
    )

    count_differences = []
    for name in sorted(resolver_names & game_names):
        left = resolver[name]
        right = game[name]
        if left is None or right is None:
            continue
        if left != right:
            if name in known:
                known_ignored.add(name)
            else:
                count_differences.append((name, left, right))

    return ParityResult(
        resolver_total=len(resolver_names),
        game_total=len(game_names),
        only_resolver=only_resolver,
        only_game=only_game,
        count_differences=tuple(count_differences),
        known_ignored=tuple(sorted(known_ignored)),
    )


@dataclass(frozen=True, slots=True)
class PropertyRow:
    """Одна строка property-дампа: значение и источник."""

    section: str
    key: str
    value: str
    file: str
    depth: str
    operation: str
    confidence: str


def _normalize_source(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return os.path.normcase(os.path.normpath(text))


def parse_property_dump(text: str, source_root=None) -> dict:
    """Разобрать property-дамп в ``(section, key) -> tuple[PropertyRow]``.

    Формат::

        section<TAB>key<TAB>value<TAB>file<TAB>depth<TAB>operation<TAB>confidence

    Лишние trailing-колонки допускаются, недостающие считаются пустыми.
    Секции и ключи сравниваются без учёта регистра, как и в кратком
    дампе. Один и тот же ключ может встречаться несколько раз: модель
    сохраняет варианты регистра и list-операции, поэтому строки
    группируются, а не перезаписывают друг друга. ``source_root``
    достраивает относительные пути ``file`` до абсолютных.
    """
    rows = {}
    root = Path(source_root) if source_root else None
    if text.startswith("\ufeff"):
        text = text[1:]
    for line_no, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            continue
        parts = line.split("\t")
        if (
            len(parts) > 1
            and parts[0].strip().lower() == "section"
            and parts[1].strip().lower() == "key"
        ):
            continue
        if len(parts) < 3:
            raise ValueError(
                "property dump: строка %d: ожидается "
                "section<TAB>key<TAB>value" % line_no
            )
        section = parts[0].strip().lower()
        key = parts[1].strip().lower()
        if not section or not key:
            raise ValueError(
                "property dump: строка %d: пустая секция или ключ" % line_no
            )
        identifier = (section, key)
        source_file = parts[3].strip() if len(parts) > 3 else ""
        if (
            root is not None
            and source_file
            and not os.path.isabs(source_file)
        ):
            source_file = str(root / source_file)
        rows.setdefault(identifier, []).append(
            PropertyRow(
                section=section,
                key=key,
                value=parts[2],
                file=source_file,
                depth=parts[4].strip() if len(parts) > 4 else "",
                operation=parts[5].strip() if len(parts) > 5 else "",
                confidence=parts[6].strip() if len(parts) > 6 else "",
            )
        )
    return {identifier: tuple(items) for identifier, items in rows.items()}


def _property_index(rows) -> dict:
    index = {}
    for (section, key), items in rows.items():
        index.setdefault(section, {})[key] = tuple(items)
    return index


def _row_signature(row):
    return (
        row.value,
        _normalize_source(row.file),
        row.operation,
        row.depth,
    )


def _display(value) -> str:
    return (
        str(value or "")
        .replace("\r", " ")
        .replace("\n", "\\n")
        .replace("\t", " ")
    )


@dataclass(frozen=True, slots=True)
class PropertyParityResult:
    """Результат property-сравнения.

    ``resolver_total``/``game_total`` — число секций, представленных
    в property-дампе (секции без ключей в нём отсутствуют).
    """

    resolver_total: int
    game_total: int
    only_resolver: tuple
    only_game: tuple
    only_resolver_keys: tuple
    only_game_keys: tuple
    key_count_differences: tuple
    value_differences: tuple
    source_differences: tuple
    source_missing: tuple
    operation_differences: tuple
    depth_differences: tuple
    row_order_differences: tuple
    known_ignored: tuple

    @property
    def ok(self) -> bool:
        return (
            not self.only_resolver
            and not self.only_game
            and not self.only_resolver_keys
            and not self.only_game_keys
            and not self.key_count_differences
            and not self.value_differences
            and not self.source_differences
            and not self.source_missing
            and not self.operation_differences
            and not self.depth_differences
            and not self.row_order_differences
        )

    def as_text(self) -> str:
        lines = [
            "resolver_sections\t%d" % self.resolver_total,
            "game_sections\t%d" % self.game_total,
            "only_resolver\t%d" % len(self.only_resolver),
            "only_game\t%d" % len(self.only_game),
            "only_resolver_keys\t%d" % len(self.only_resolver_keys),
            "only_game_keys\t%d" % len(self.only_game_keys),
            "key_count_differences\t%d" % len(self.key_count_differences),
            "value_differences\t%d" % len(self.value_differences),
            "source_differences\t%d" % len(self.source_differences),
            "source_missing\t%d" % len(self.source_missing),
            "operation_differences\t%d" % len(self.operation_differences),
            "depth_differences\t%d" % len(self.depth_differences),
            "row_order_differences\t%d" % len(self.row_order_differences),
            "known_ignored\t%d" % len(self.known_ignored),
        ]
        for name in self.only_resolver:
            lines.append("only_resolver\t%s" % name)
        for name in self.only_game:
            lines.append("only_game\t%s" % name)
        for section, key in self.only_resolver_keys:
            lines.append("only_resolver_key\t%s\t%s" % (section, key))
        for section, key in self.only_game_keys:
            lines.append("only_game_key\t%s\t%s" % (section, key))
        for section, key, left, right in self.key_count_differences:
            lines.append(
                "key_count_difference\t%s\t%s\t%s\t%s"
                % (section, key, left, right)
            )
        for section, key, left, right in self.value_differences:
            lines.append(
                "value_difference\t%s\t%s\t%s\t%s"
                % (section, key, _display(left), _display(right))
            )
        for section, key, left, right in self.source_differences:
            lines.append(
                "source_difference\t%s\t%s\t%s\t%s"
                % (section, key, _display(left), _display(right))
            )
        for section, key, left, right in self.source_missing:
            lines.append(
                "source_missing\t%s\t%s\t%s\t%s"
                % (section, key, _display(left), _display(right))
            )
        for section, key, left, right in self.operation_differences:
            lines.append(
                "operation_difference\t%s\t%s\t%s\t%s"
                % (section, key, _display(left), _display(right))
            )
        for section, key, left, right in self.depth_differences:
            lines.append(
                "depth_difference\t%s\t%s\t%s\t%s"
                % (section, key, _display(left), _display(right))
            )
        for section, key in self.row_order_differences:
            lines.append("row_order_difference\t%s\t%s" % (section, key))
        for name in self.known_ignored:
            lines.append("known_ignored\t%s" % name)
        return "\n".join(lines) + "\n"


def compare_property_dumps(resolver, game,
                           known: Optional[Iterable[str]] = None,
                           compare_row_order: bool = False
                           ) -> PropertyParityResult:
    """Сравнить два property-дампа по секциям, ключам, значениям
    и источникам.

    ``known`` — поимённый реестр секций, отличия которых уже приняты.
    Для известных секций отличия не попадают в результат, но сама
    секция отмечается в ``known_ignored``, если отличие действительно
    было. Поля ``operation`` и ``depth`` сравниваются только если
    заполнены с обеих сторон; отсутствие ``file`` на одной стороне
    попадает в ``source_missing`` и является различием. Повторяющиеся
    ключи сравниваются как набор строк: разное количество строк
    попадает в ``key_count_differences``, значения, источники,
    операции и глубина — в соответствующие списки.

    При ``compare_row_order=True`` дополнительно проверяется порядок
    повторяющихся строк одного ключа (list-операции); расхождение
    попадает в ``row_order_differences``. По умолчанию порядок не
    проверяется, чтобы не зависеть от сортировки внешнего дампа.
    """
    known = {str(name).lower() for name in (known or ())}
    resolver_index = _property_index(resolver)
    game_index = _property_index(game)
    resolver_sections = set(resolver_index)
    game_sections = set(game_index)

    only_resolver = tuple(
        sorted(name for name in resolver_sections - game_sections
               if name not in known)
    )
    only_game = tuple(
        sorted(name for name in game_sections - resolver_sections
               if name not in known)
    )
    known_ignored = set(
        (resolver_sections ^ game_sections) & known
    )

    only_resolver_keys = []
    only_game_keys = []
    key_count_differences = []
    value_differences = []
    source_differences = []
    source_missing = []
    operation_differences = []
    depth_differences = []
    row_order_differences = []

    for section in sorted(resolver_sections & game_sections):
        resolver_keys = resolver_index[section]
        game_keys = game_index[section]
        accepted = section in known
        section_differs = False

        for key in sorted(set(resolver_keys) - set(game_keys)):
            section_differs = True
            if not accepted:
                only_resolver_keys.append((section, key))
        for key in sorted(set(game_keys) - set(resolver_keys)):
            section_differs = True
            if not accepted:
                only_game_keys.append((section, key))

        for key in sorted(set(resolver_keys) & set(game_keys)):
            left_rows = sorted(
                resolver_keys[key],
                key=_row_signature,
            )
            right_rows = sorted(
                game_keys[key],
                key=_row_signature,
            )
            if compare_row_order:
                ordered_left = tuple(
                    _row_signature(row) for row in resolver_keys[key]
                )
                ordered_right = tuple(
                    _row_signature(row) for row in game_keys[key]
                )
                if (
                    ordered_left != ordered_right
                    and sorted(ordered_left) == sorted(ordered_right)
                ):
                    section_differs = True
                    if not accepted:
                        row_order_differences.append((section, key))
            if len(left_rows) != len(right_rows):
                section_differs = True
                if not accepted:
                    key_count_differences.append(
                        (section, key, len(left_rows), len(right_rows))
                    )
            for left, right in zip(left_rows, right_rows):
                if left.value != right.value:
                    section_differs = True
                    if not accepted:
                        value_differences.append(
                            (section, key, left.value, right.value)
                        )
                left_source = left.file
                right_source = right.file
                if bool(left_source) != bool(right_source):
                    section_differs = True
                    if not accepted:
                        source_missing.append(
                            (section, key, left_source, right_source)
                        )
                elif (
                    left_source
                    and right_source
                    and _normalize_source(left_source)
                    != _normalize_source(right_source)
                ):
                    section_differs = True
                    if not accepted:
                        source_differences.append(
                            (section, key, left_source, right_source)
                        )
                if (
                    left.operation
                    and right.operation
                    and left.operation != right.operation
                ):
                    section_differs = True
                    if not accepted:
                        operation_differences.append(
                            (section, key, left.operation, right.operation)
                        )
                if (
                    left.depth
                    and right.depth
                    and left.depth != right.depth
                ):
                    section_differs = True
                    if not accepted:
                        depth_differences.append(
                            (section, key, left.depth, right.depth)
                        )

        if accepted and section_differs:
            known_ignored.add(section)

    return PropertyParityResult(
        resolver_total=len(resolver_sections),
        game_total=len(game_sections),
        only_resolver=only_resolver,
        only_game=only_game,
        only_resolver_keys=tuple(sorted(only_resolver_keys)),
        only_game_keys=tuple(sorted(only_game_keys)),
        key_count_differences=tuple(sorted(key_count_differences)),
        value_differences=tuple(sorted(value_differences)),
        source_differences=tuple(sorted(source_differences)),
        source_missing=tuple(sorted(source_missing)),
        operation_differences=tuple(sorted(operation_differences)),
        depth_differences=tuple(sorted(depth_differences)),
        row_order_differences=tuple(sorted(row_order_differences)),
        known_ignored=tuple(sorted(known_ignored)),
    )


__all__ = [
    "PROPERTY_HEADER",
    "ParityResult",
    "PropertyParityResult",
    "PropertyRow",
    "compare_property_dumps",
    "compare_section_dumps",
    "dump_section_summary",
    "load_known_differences",
    "parse_manifest",
    "parse_property_dump",
    "parse_section_dump",
]
