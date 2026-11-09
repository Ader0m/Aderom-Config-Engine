# -*- coding: utf-8 -*-
"""Модель include-связей для дерева отображения.

Это **не** загрузчик LTX и не замена :class:`Resolver4`. Модуль разбирает
``#include`` в сырых файлах, чтобы UI мог построить дерево зависимостей до
запуска резолвера. Фактическую загрузку и DLTX-семантику выполняет только
``Resolver4``; здесь нет merge/sort/depth-логики движка.

Правила намеренно простые и проверяемые: цитаты, bare-форма, маски
``*``/``?``. Если они разойдутся с резолвером, это должно ловиться
parity-тестом, а не «подгонкой» резолвера.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.tools.fs import normalize_path, pattern_match, strip_comments


INCLUDE_QUOTED_RE = re.compile(r'#include\s+"([^"]+)"', re.IGNORECASE)
INCLUDE_BARE_RE = re.compile(r'#include\s+([^\s;]+)', re.IGNORECASE)


def extract_includes(text: str):
    """Вернуть список include-директив из текста LTX."""
    out = []

    for line in text.splitlines():
        clean = strip_comments(line)

        if not clean:
            continue

        if not clean.startswith("#"):
            continue

        if "include" not in clean.lower():
            continue

        match = INCLUDE_QUOTED_RE.search(clean)
        if match:
            out.append(match.group(1))
            continue

        match = INCLUDE_BARE_RE.search(clean)
        if match:
            out.append(match.group(1).strip('"'))

    return out


def resolve_include_targets(inc_name: str, parent_path):
    """Разрешить ``#include`` относительно файла-родителя.

    Поддерживаются маски ``*`` и ``?``. Возвращает только существующие
    файлы; ничего не загружает и не меняет.
    """
    inc = str(inc_name or "").strip().strip('"').replace("\\", "/")

    if not inc:
        return []

    parent_dir = Path(parent_path).parent
    path = Path(inc)

    if path.is_absolute():
        if "*" in inc or "?" in inc:
            directory = path.parent
            mask = path.name

            if not directory.is_dir():
                return []

            return sorted(
                (
                    normalize_path(entry)
                    for entry in directory.iterdir()
                    if entry.is_file()
                    and entry.suffix.lower() == ".ltx"
                    and pattern_match(entry.name, mask)
                ),
                key=lambda item: str(item).lower(),
            )

        found = normalize_path(path)
        return [found] if found.is_file() else []

    inc_rel = inc.lstrip("/")

    if "*" in inc_rel or "?" in inc_rel:
        parts = inc_rel.split("/")
        mask = parts[-1]
        subdir = "/".join(parts[:-1])

        directory = parent_dir / subdir if subdir else parent_dir

        if not directory.is_dir():
            return []

        return sorted(
            (
                normalize_path(entry)
                for entry in directory.iterdir()
                if entry.is_file()
                and entry.suffix.lower() == ".ltx"
                and pattern_match(entry.name, mask)
            ),
            key=lambda item: str(item).lower(),
        )

    found = normalize_path(parent_dir / inc_rel)
    if found.is_file():
        return [found]

    found = normalize_path(parent_dir / inc)
    return [found] if found.is_file() else []
