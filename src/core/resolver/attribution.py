# -*- coding: utf-8 -*-
"""Донасыщение результата резолвера для UI.

Модуль **только читает** данные ``Resolver4`` и дерево. Он не меняет
``Item``/``Sect``/``r.data`` и не удаляет источники: все функции возвращают
новые строки, словари или подписи.

Здесь собраны:

- карта источников ``Item.filename -> человекочитаемая подпись``;
- подпись одного источника;
- классификация физического пути (``MO2\\...`` / ``GAME\\...``).
"""

from __future__ import annotations

import os
from pathlib import Path


def build_source_map(forest, field="real_paths") -> dict:
    """Карта источников для резолвера.

    Ключи — нижнерегистровые stem'ы файлов (как ожидает ``pretty_source``).
    Значения — человекочитаемая подпись из запрошенной таблицы леса:
    ``field="real_paths"`` (по умолчанию) — физическое расположение
    (MO2/GAME); ``field="rel_paths"`` — игровой VFS-вид.
    """
    table = getattr(forest, field, None) or {}
    mapping = {}

    for path in forest.files:
        display = table.get(path) or forest.rel_paths.get(path, str(path))

        key = os.path.splitext(str(path).lower())[0]
        mapping[key] = display

        base_key = os.path.splitext(path.name.lower())[0]
        if base_key not in mapping:
            mapping[base_key] = display

    return mapping


def pretty_source(source, source_map) -> str:
    """Вернуть человекочитаемый источник по данным из ``source_map``."""
    if not source:
        return "?"

    value = str(source).lower()

    if value in source_map:
        return source_map[value]

    without_ext = os.path.splitext(value)[0]
    if without_ext in source_map:
        return source_map[without_ext]

    base = os.path.basename(without_ext)
    return source_map.get(base, source)


def describe_real_path(real: Path, game_root_real: Path) -> str:
    """Человекочитаемое описание фактического расположения файла.

    ``MO2\\<мод>\\<путь>`` — если файл лежит в моде MO2;
    ``GAME\\<путь>`` — если файл оригинальный;
    абсолютный путь — во всех остальных случаях.
    """
    parts = real.parts
    low = [part.lower() for part in parts]

    if "mods" in low:
        index = low.index("mods")
        if index + 1 < len(parts):
            mod = parts[index + 1]
            rel = parts[index + 2:]
            if rel:
                return "MO2\\%s\\%s" % (mod, "\\".join(rel))
            return "MO2\\%s" % mod

    try:
        rel = os.path.relpath(str(real), str(game_root_real))
        if not rel.startswith(".."):
            return "GAME\\%s" % rel
    except ValueError:
        pass

    return str(real)
