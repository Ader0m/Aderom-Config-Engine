# -*- coding: utf-8 -*-
"""Разбор секции и поиск в её полях путей к .ogg."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from sdk.paths import path_key

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


def _section_field_refs(sections, section: str):
    """Поля уже разобранного документа с номерами строк."""
    target = normalize_section_name(section).lower()
    if not target or target not in sections:
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


def parse_section_field_refs(text: str, section: str):
    """Поля секции с номерами строк и учётом наследования."""
    return _section_field_refs(_parse_document(text), section)


def make_section_resolver(text: str):
    """Вернуть функцию ``имя секции -> поля`` для одного документа.

    Документ разбирается один раз: это важно, когда звуковые поля
    ссылаются на другие секции (``snd_shoot = wpn_snd_shoot``) и
    приходится разрешать их рекурсивно.
    """
    sections = _parse_document(text)

    def resolve(section):
        return _section_field_refs(sections, section)

    # Тот же разбор без повторного парсинга текста: пригодится,
    # когда из документа нужны и поля, и номера строк.
    resolve.field_refs = lambda section: _section_field_refs(
        sections,
        section,
    )
    return resolve


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


def _add_file(result: dict, path: Path, include_missing: bool = False):
    try:
        if path.suffix.lower() != ".ogg":
            return
        if include_missing or path.is_file():
            key = path_key(path)
            result[key] = path
    except OSError:
        pass


def _resolve_sound_section(token: str, resolve_section, visited):
    """Развернуть имя секции в её звуковые поля.

    В X-Ray ``snd_shoot`` может ссылаться не на файл, а на секцию
    звуков (``wpn_bm16full_snd_shoot``), где лежат слои
    ``snd_1_layer``/``snd_2_layer``/``snd_3_layer``. Возвращает поля
    такой секции или ``None``, если токен — не звуковая секция.
    """
    if resolve_section is None:
        return None
    if not token or "\\" in token or "/" in token:
        return None
    if token.lower().endswith(".ogg"):
        return None

    name = normalize_section_name(token).lower()
    if not name or name in visited:
        return None

    try:
        fields = list(resolve_section(name) or ())
    except Exception:
        return None
    if not fields:
        return None

    # Обычные секции предметов тоже находятся резолвером, но
    # разворачивать имеет смысл только звуковые (есть snd_*-поля).
    if not any(
        str(key).lower().startswith("snd_")
        for key, _value in _field_pairs(fields)
    ):
        return None

    visited.add(name)
    return fields


def _iter_fields_paths(
    fields,
    sounds_root: Path,
    include_missing: bool,
    resolve_section,
    visited,
):
    for key, value in _field_pairs(fields):
        is_sound_field = key.lower().startswith("snd_")
        for path in _iter_sound_paths(
            value,
            sounds_root,
            is_sound_field,
            include_missing=include_missing,
            resolve_section=resolve_section,
            visited=visited,
        ):
            yield path


def _iter_token_paths(
    token: str,
    sounds_root: Path,
    is_sound_field: bool,
    include_missing: bool,
):
    token = token.replace("/", "\\").lstrip("\\")
    lowered = token.lower()

    for prefix in ("gamedata\\sounds\\", "sounds\\"):
        if lowered.startswith(prefix):
            token = token[len(prefix):]
            lowered = token.lower()
            break

    if not token or token in (".", ".."):
        return
    if lowered in (
        "none",
        "null",
        "-",
        "0",
        "$no_sound",
        "no_sound",
    ):
        return

    first = token.split("\\", 1)[0]
    # Для snd_* допускаем путь от корня sounds без подкаталога:
    # например, snd_shoot = wpn_ak74_snd_shoot.
    if not first or (
        not (sounds_root / first).is_dir()
        and not is_sound_field
    ):
        return

    has_separator = "\\" in token

    if "*" in token or "?" in token:
        pattern = token.replace("\\", "/")
        for match in sounds_root.glob(pattern):
            if match.is_dir():
                for nested in match.rglob("*.ogg"):
                    yield nested
            else:
                yield match
        return

    candidate = sounds_root.joinpath(*token.split("\\"))
    if candidate.is_dir():
        for nested in candidate.rglob("*.ogg"):
            yield nested
        return

    if candidate.suffix.lower() == ".ogg":
        if candidate.is_file() or (include_missing and is_sound_field):
            yield candidate
        return

    if not candidate.suffix:
        missing_ok = include_missing and is_sound_field and has_separator
        variants = (
            candidate.with_suffix(".ogg"),
            Path(str(candidate) + ".ogg"),
        )
        for path in variants:
            if path.is_file() or missing_ok:
                yield path


def _iter_sound_paths(
    value: str,
    sounds_root: Path,
    is_sound_field: bool,
    include_missing: bool = False,
    resolve_section=None,
    visited=None,
):
    """Развернуть значение поля в .ogg-файлы.

    ``resolve_section`` позволяет превратить ссылку на звуковую
    секцию (``snd_shoot = wpn_snd_shoot``) в её слои.
    ``include_missing`` оставляет ссылки, которых нет в файловой
    системе (например, звук упакован в игровой ``.db``-архив).
    """
    if visited is None:
        visited = set()

    for token in _tokens(value):
        section_fields = None
        if is_sound_field:
            section_fields = _resolve_sound_section(
                token,
                resolve_section,
                visited,
            )
        if section_fields is not None:
            for path in _iter_fields_paths(
                section_fields,
                sounds_root,
                include_missing,
                resolve_section,
                visited,
            ):
                yield path
            continue

        for path in _iter_token_paths(
            token,
            sounds_root,
            is_sound_field,
            include_missing,
        ):
            yield path


def extract_sound_paths(
    fields,
    sounds_root,
    resolve_section=None,
    include_missing: bool = False,
) -> List[Path]:
    """Найти .ogg-файлы в значениях полей секции.

    Быстрая проверка: первый сегмент пути должен быть существующей
    папкой внутри ``gamedata/sounds``. Это отсекает обычные числа,
    имена и служебные значения.

    ``resolve_section`` раскрывает ссылки на звуковые секции.
    ``include_missing`` оставляет пути, которых нет в VFS — чтобы
    пользователь видел все ссылки секции, а не только распакованные.
    """
    if sounds_root is None:
        return []

    sounds_root = Path(sounds_root)
    if not sounds_root.is_dir():
        return []

    result = {}

    for key, value in _field_pairs(fields):
        is_sound_field = key.lower().startswith("snd_")
        for path in _iter_sound_paths(
            value,
            sounds_root,
            is_sound_field,
            include_missing=include_missing,
            resolve_section=resolve_section,
        ):
            _add_file(result, path, include_missing)

    return sorted(
        result.values(),
        key=lambda path: str(path).lower(),
    )


def extract_sound_references(
    text,
    section,
    sounds_root,
    source_path=None,
    resolve_section=None,
):
    """Ссылки на .ogg с полями и номерами строк (для графа связей)."""
    if sounds_root is None:
        return []

    sounds_root = Path(sounds_root)
    if not sounds_root.is_dir():
        return []

    result = {}

    refs_getter = getattr(resolve_section, "field_refs", None)
    if callable(refs_getter):
        field_refs = refs_getter(section)
    else:
        field_refs = parse_section_field_refs(text, section)

    for key, value, line_number, line_text in field_refs:
        is_sound_field = key.lower().startswith("snd_")
        # Для каждого поля — свой набор посещённых секций: одно и то
        # же звуковое имя может встречаться в snd_shoot и snd_shoot_1,
        # и обе ссылки должны попасть в граф.
        for path in _iter_sound_paths(
            value,
            sounds_root,
            is_sound_field,
            include_missing=False,
            resolve_section=resolve_section,
            visited=set(),
        ):
            try:
                if not path.is_file() or path.suffix.lower() != ".ogg":
                    continue
            except OSError:
                continue

            key_norm = path_key(path)
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
