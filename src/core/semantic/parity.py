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
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


SUMMARY_HEADER = "section\tkey_count"


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
    data = json.loads(Path(path).read_text(encoding="utf-8"))
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
    data = json.loads(Path(path).read_text(encoding="utf-8"))
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
    """Сравнить два ``name -> count|None`` словаря."""
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
    known_ignored = tuple(
        sorted(
            (resolver_names ^ game_names) & known
        )
    )

    count_differences = []
    for name in sorted(resolver_names & game_names):
        left = resolver[name]
        right = game[name]
        if left is None or right is None:
            continue
        if left != right:
            count_differences.append((name, left, right))

    return ParityResult(
        resolver_total=len(resolver_names),
        game_total=len(game_names),
        only_resolver=only_resolver,
        only_game=only_game,
        count_differences=tuple(count_differences),
        known_ignored=known_ignored,
    )


__all__ = [
    "ParityResult",
    "compare_section_dumps",
    "dump_section_summary",
    "load_known_differences",
    "parse_manifest",
    "parse_section_dump",
]
