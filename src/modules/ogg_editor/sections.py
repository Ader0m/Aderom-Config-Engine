# -*- coding: utf-8 -*-
"""Разбор секции и поиск в её полях путей к .ogg."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

_SECTION_RE = re.compile(r"^\[([^\]]+)\](?::.*)?$")
_KEY_RE = re.compile(r"^([^=]+?)\s*=\s*(.*)$")
_SPLIT_RE = re.compile(r"[\s,;|]+")


def normalize_section_name(value: str) -> str:
    """Привести имя секции к виду без скобок.

    Пользователь может ввести ``wpn``, ``[wpn]`` или ``[wpn]:parent`` —
    для поиска все варианты означают одну и ту же секцию ``wpn``.
    """
    text = str(value or "").strip()
    if not text:
        return ""

    if text.startswith("["):
        end = text.find("]")
        if end >= 0:
            return text[1:end].strip()
        text = text[1:]

    if text.endswith("]"):
        text = text[:-1]
    return text.strip()


def section_label(value: str) -> str:
    """Человекочитаемая подпись секции: ``wpn`` -> ``[wpn]``."""
    name = normalize_section_name(value)
    return "[%s]" % name if name else ""


@dataclass(frozen=True)
class SoundReference:
    """Ссылка на .ogg из конкретного поля секции."""

    path: Path
    field: str
    value: str
    line_number: int
    line_text: str
    source_path: Optional[Path] = None


def _parse_document(text: str):
    """Разобрать все секции документа: поля, строки и родителей."""
    sections = {}
    current = None

    for line_number, line in enumerate(str(text or "").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith((";", "//")):
            continue

        if stripped.startswith("["):
            match = _SECTION_RE.match(stripped)
            if match is None:
                current = None
                continue

            name = match.group(1).strip().lower()
            parents = ()
            if "]:" in stripped:
                raw_parents = stripped.split("]:", 1)[1]
                parents = tuple(
                    parent.strip().lower()
                    for parent in raw_parents.split(",")
                    if parent.strip()
                )
            data = sections.setdefault(
                name,
                {"parents": (), "fields": []},
            )
            if parents:
                data["parents"] = parents
            current = name
            continue

        if current is None:
            continue

        match = _KEY_RE.match(line)
        if match is None:
            continue
        sections[current]["fields"].append(
            (
                match.group(1).strip(),
                match.group(2).strip(),
                line_number,
                line.strip(),
            )
        )

    return sections


def parse_section_field_refs(text: str, section: str):
    """Поля секции с номерами строк и учётом наследования."""
    target = normalize_section_name(section).lower()
    if not target:
        return []

    sections = _parse_document(text)
    if target not in sections:
        return []

    merged = {}
    order = []

    def add(name, stack):
        if name in stack:
            return
        data = sections.get(name)
        if data is None:
            return
        for parent in data["parents"]:
            add(parent, stack | {name})
        for key, value, line_number, line_text in data["fields"]:
            if key not in merged:
                order.append(key)
            merged[key] = (value, line_number, line_text)

    add(target, set())
    return [
        (key, *merged[key])
        for key in order
    ]


def parse_section_fields(text: str, section: str) -> List[Tuple[str, str]]:
    """Вернуть эффективные поля секции с учётом наследования."""
    return [
        (key, value)
        for key, value, _line_number, _line_text in parse_section_field_refs(
            text,
            section,
        )
    ]


def _field_value(field) -> str:
    if hasattr(field, "value"):
        return str(getattr(field, "value", "") or "")
    if isinstance(field, (tuple, list)) and len(field) >= 2:
        return str(field[1])
    return ""


def _field_pairs(fields) -> Iterable[Tuple[str, str]]:
    for field in fields:
        if hasattr(field, "key"):
            yield (
                str(getattr(field, "key", "") or ""),
                str(getattr(field, "value", "") or ""),
            )
        elif isinstance(field, (tuple, list)) and len(field) >= 2:
            yield str(field[0]), str(field[1])


def _tokens(value: str):
    text = str(value or "").strip().strip("\"'")
    for token in _SPLIT_RE.split(text):
        token = token.strip().strip("\"'")
        if token:
            yield token


def _add_file(result: dict, path: Path):
    try:
        if path.is_file() and path.suffix.lower() == ".ogg":
            key = os.path.normcase(os.path.abspath(str(path)))
            result[key] = path
    except OSError:
        pass


def _iter_sound_paths(value: str, sounds_root: Path, is_sound_field: bool):
    """Развернуть значение поля в существующие .ogg-файлы."""
    for token in _tokens(value):
        token = token.replace("/", "\\").lstrip("\\")
        lowered = token.lower()

        for prefix in ("gamedata\\sounds\\", "sounds\\"):
            if lowered.startswith(prefix):
                token = token[len(prefix):]
                lowered = token.lower()
                break

        if not token or token in (".", ".."):
            continue
        if lowered in (
            "none",
            "null",
            "-",
            "0",
            "$no_sound",
            "no_sound",
        ):
            continue

        first = token.split("\\", 1)[0]
        # Для snd_* допускаем путь от корня sounds без подкаталога:
        # например, snd_shoot = wpn_ak74_snd_shoot.
        if not first or (
            not (sounds_root / first).is_dir()
            and not is_sound_field
        ):
            continue

        if "*" in token or "?" in token:
            pattern = token.replace("\\", "/")
            for match in sounds_root.glob(pattern):
                if match.is_dir():
                    for nested in match.rglob("*.ogg"):
                        yield nested
                else:
                    yield match
            continue

        candidate = sounds_root.joinpath(*token.split("\\"))
        if candidate.is_dir():
            for nested in candidate.rglob("*.ogg"):
                yield nested
            continue

        if candidate.suffix.lower() == ".ogg":
            yield candidate
            continue

        if not candidate.suffix:
            yield candidate.with_suffix(".ogg")
            yield Path(str(candidate) + ".ogg")


def extract_sound_paths(fields, sounds_root) -> List[Path]:
    """Найти .ogg-файлы в значениях полей секции.

    Быстрая проверка: первый сегмент пути должен быть существующей
    папкой внутри ``gamedata/sounds``. Это отсекает обычные числа,
    имена и служебные значения.
    """
    if sounds_root is None:
        return []

    sounds_root = Path(sounds_root)
    if not sounds_root.is_dir():
        return []

    result = {}

    for key, value in _field_pairs(fields):
        is_sound_field = key.lower().startswith("snd_")
        for path in _iter_sound_paths(value, sounds_root, is_sound_field):
            _add_file(result, path)

    return sorted(
        result.values(),
        key=lambda path: str(path).lower(),
    )


def extract_sound_references(text, section, sounds_root, source_path=None):
    """Ссылки на .ogg с полями и номерами строк (для графа связей)."""
    if sounds_root is None:
        return []

    sounds_root = Path(sounds_root)
    if not sounds_root.is_dir():
        return []

    result = {}

    for key, value, line_number, line_text in parse_section_field_refs(
        text,
        section,
    ):
        is_sound_field = key.lower().startswith("snd_")
        for path in _iter_sound_paths(value, sounds_root, is_sound_field):
            try:
                if not path.is_file() or path.suffix.lower() != ".ogg":
                    continue
            except OSError:
                continue

            key_norm = os.path.normcase(os.path.abspath(str(path)))
            entry = result.setdefault(
                key_norm,
                {"path": path, "items": []},
            )
            item = (
                key,
                value,
                int(line_number),
                str(line_text),
            )
            if item not in entry["items"]:
                entry["items"].append(item)

    references = []
    for entry in sorted(
        result.values(),
        key=lambda item: str(item["path"]).lower(),
    ):
        for key, value, line_number, line_text in entry["items"]:
            references.append(
                SoundReference(
                    path=entry["path"],
                    field=key,
                    value=value,
                    line_number=line_number,
                    line_text=line_text,
                    source_path=source_path,
                )
            )
    return references
