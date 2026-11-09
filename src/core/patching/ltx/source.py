# -*- coding: utf-8 -*-
"""Общие операции с LTX-файлами-победителями.

Модуль вынесен из ``ltx_override``/``ltx_in_place``: оба режима одинаково
ищут физический файл строки, номер строки в нём и применяют замены. Здесь
только чтение/поиск/замена; планирование и предупреждения остаются в
сервисах.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from core.patching.ltx.changes import (
    ResolvedLineChange,
    analyze_resolved_changes,
    line_context,
)
from core.patching.ltx.document import (
    parse_key_line,
    parse_section_header,
    parse_value,
)
from core.patching.targets import xml_relative_path
from core.tools.fs import strip_comments


# ---------------------------------------------------------------- путь


def mods_roots(config_dir: Path) -> List[Path]:
    """Каталоги ``mods`` рядом с выбранным configs (MO2 и совместимые)."""
    bases = [Path(config_dir)]
    try:
        bases.extend(list(Path(config_dir).parents)[:6])
    except Exception:
        pass

    roots: List[Path] = []
    seen = set()

    for base in bases:
        if base.name.lower() == "mods" and base.is_dir():
            candidates = [base]
        else:
            candidates = [
                base / "mods",
                base / "MO2" / "mods",
                base / "ModOrganizer" / "mods",
                base / "ModOrganizer2" / "mods",
            ]

        for candidate in candidates:
            if not candidate.is_dir():
                continue
            try:
                key = str(candidate.resolve()).lower()
            except OSError:
                key = str(candidate).lower()
            if key in seen:
                continue
            seen.add(key)
            roots.append(candidate)

    return roots


def resolve_source_path(
    source,
    source_path: Path,
    forest=None,
    config_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Найти физический путь победителя, даже если ``LineSource.path`` пуст."""
    path = getattr(source, "path", None)
    if path is not None:
        candidate = Path(path)
        if candidate.is_file():
            return candidate

    display = str(getattr(source, "display", "") or "").strip()
    if not display:
        return None

    normalized = display.replace("/", "\\")
    if config_dir is None:
        config_dir = (
            Path(getattr(forest, "scan_dir"))
            if forest is not None and getattr(forest, "scan_dir", None)
            else source_path.parent
        )

    upper = normalized.upper()
    if upper.startswith("GAME\\"):
        candidate = Path(config_dir) / normalized[5:]
        if candidate.is_file():
            return candidate

    if upper.startswith("MO2\\"):
        rest = normalized[4:]
        parts = rest.split("\\", 1)
        if len(parts) == 2:
            mod_name, rel = parts
            for mods_root in mods_roots(config_dir):
                candidate = mods_root / mod_name / rel
                if candidate.is_file():
                    return candidate

    candidate = Path(display)
    if candidate.is_file():
        return candidate

    return None


def winner_relative_path(
    source,
    winner: Path,
    forest=None,
    config_dir: Optional[Path] = None,
) -> Path:
    """Путь файла-победителя относительно ``gamedata/configs``."""
    display = str(getattr(source, "display", "") or "").strip()
    normalized = display.replace("/", "\\")
    upper = normalized.upper()

    if upper.startswith("GAME\\"):
        return Path(normalized[5:])

    if upper.startswith("MO2\\"):
        rest = normalized[4:]
        parts = rest.split("\\", 1)
        if len(parts) == 2:
            rel = parts[1]
            marker = "gamedata\\configs\\"
            low = rel.lower()
            index = low.find(marker)
            if index >= 0:
                return Path(rel[index + len(marker):])
            return Path(rel)

    if ":" in display and "\\gamedata\\configs\\" in normalized.lower():
        low = normalized.lower()
        marker = "\\gamedata\\configs\\"
        index = low.find(marker)
        return Path(normalized[index + len(marker):])

    relative = xml_relative_path(forest, winner)
    if relative.name == winner.name and len(relative.parts) == 1:
        # xml_relative_path не нашёл файл в дереве; пробуем физический путь.
        if config_dir is not None:
            try:
                return Path(winner).relative_to(
                    Path(config_dir).parent / "configs"
                )
            except ValueError:
                pass
    return relative


# ---------------------------------------------------------------- чтение


def read_ltx(path: Path) -> Tuple[str, str]:
    raw = Path(path).read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace"), "utf-8-sig"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("windows-1251", errors="replace"), "windows-1251"


def split_line_ending(line: str) -> Tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    if line.endswith("\r"):
        return line[:-1], "\r"
    return line, ""


def find_winner_line(
    path: Path,
    old_line: str,
    used: Optional[set] = None,
) -> int:
    """Найти номер строки в файле-победителе по её тексту."""
    text, _encoding = read_ltx(path)
    needle = str(old_line or "").strip()
    if not needle:
        return 0
    used = used or set()
    for number, line in enumerate(text.splitlines(), 1):
        if number in used:
            continue
        if line.strip() == needle:
            return number
    return 0


def find_section_key_line(
    path: Path,
    section: Optional[str],
    key: Optional[str],
    used: Optional[set] = None,
) -> int:
    """Найти строку ключа по секции и имени.

    Нужен, когда resolved-строка не совпадает с физической строкой
    победителя (например, из-за уже применённого DLTX-патча).
    """
    if not section or not key:
        return 0

    text, _encoding = read_ltx(path)
    used = used or set()
    current = None
    wanted_section = str(section).lower()
    wanted_key = str(key).lower()

    for number, line in enumerate(text.splitlines(), 1):
        clean = strip_comments(line).strip()
        if not clean:
            continue

        header = parse_section_header(clean)
        if header is not None:
            current = header[1]
            continue

        parsed = parse_key_line(clean)
        if parsed is None:
            continue

        operation, found_key, _raw, has_value = parsed
        if (
            operation == "set"
            and has_value
            and current == wanted_section
            and found_key.lower() == wanted_key
            and number not in used
        ):
            return number

    return 0


def apply_replacements(
    path: Path,
    replacements: Dict[int, str],
) -> Tuple[str, str]:
    """Заменить строки файла, сохранив их переводы строк."""
    text, encoding = read_ltx(path)
    lines = text.splitlines(keepends=True)

    for line_number, new_text in sorted(
        replacements.items(),
        reverse=True,
    ):
        index = int(line_number) - 1
        if index < 0 or index >= len(lines):
            continue
        _old, ending = split_line_ending(lines[index])
        lines[index] = str(new_text) + ending

    return "".join(lines), encoding


def replace_values(
    text: str,
    updates: Sequence[Tuple[str, str, str]],
) -> str:
    """Заменить значения ключей в LTX-тексте, сохранив форматирование.

    ``updates`` — тройки ``(секция, ключ, новое_значение)``. Меняется вся
    строка значения, как этого требуют In-place/Override.
    """
    remaining = {
        (str(section).lower(), str(key).lower()): value
        for section, key, value in updates
    }
    if not remaining:
        return text

    lines = text.splitlines(keepends=True)
    current = None

    for index, line in enumerate(lines):
        body, ending = split_line_ending(line)
        clean = strip_comments(body).strip()
        if not clean:
            continue

        header = parse_section_header(clean)
        if header is not None:
            current = header[1]
            continue

        parsed = parse_key_line(clean)
        if parsed is None:
            continue
        operation, key, _raw, has_value = parsed
        if operation != "set" or not has_value:
            continue

        marker = (str(current).lower(), key.lower())
        if marker not in remaining:
            continue

        leading = body[: len(body) - len(body.lstrip())]
        lines[index] = (
            leading
            + key
            + " = "
            + str(remaining.pop(marker))
            + ending
        )

        if not remaining:
            break

    return "".join(lines)


# ---------------------------------------------------------------- изменения


def line_changes(
    baseline_text: str,
    edited_text: str,
    line_sources,
) -> Tuple[ResolvedLineChange, ...]:
    """Изменённые строки с быстрым путём для равной длины.

    Полный ``SequenceMatcher`` на resolved-файле в миллион строк стоит
    секунды. Если число строк не изменилось, достаточно позиционного
    сравнения.
    """
    old_lines = str(baseline_text or "").replace(
        "\r\n", "\n"
    ).replace("\r", "\n").split("\n")
    new_lines = str(edited_text or "").replace(
        "\r\n", "\n"
    ).replace("\r", "\n").split("\n")

    if len(old_lines) != len(new_lines):
        return tuple(
            analyze_resolved_changes(
                baseline_text,
                edited_text,
                line_sources,
            ).changes
        )

    sources = list(line_sources or ())
    sections, keys, section_lines, key_lines = line_context(old_lines)
    changes: List[ResolvedLineChange] = []

    for index, (old_line, new_line) in enumerate(zip(old_lines, new_lines)):
        if old_line == new_line:
            continue

        new_clean = strip_comments(new_line).strip()
        section = sections[index] if index < len(sections) else None
        key = keys[index] if index < len(keys) else None
        source = sources[index] if index < len(sources) else None

        new_header = parse_section_header(new_clean)
        new_key = parse_key_line(new_clean)

        if (
            (index < len(section_lines) and section_lines[index])
            or new_header is not None
        ):
            kind = "structural"
            note = "изменение заголовка секции не поддержано"
        elif (
            index < len(key_lines)
            and key_lines[index]
            and new_key is not None
            and new_key[1] == key
            and section
            and new_key[0] == "set"
        ):
            kind = "value"
            note = ""
        else:
            kind = "unsupported"
            note = "изменение не является заменой значения ключа"

        changes.append(
            ResolvedLineChange(
                line_number=index + 1,
                old_line=old_line,
                new_line=new_line,
                section=section,
                key=key,
                source=source,
                kind=kind,
                note=note,
            )
        )

    return tuple(changes)


def value_from_new_line(
    change: ResolvedLineChange,
) -> Tuple[Optional[str], Optional[str]]:
    """Вернуть ``(ключ, значение)`` из полной новой строки пользователя."""
    text = strip_comments(str(change.new_line or "")).strip()
    if not text or "\n" in text or "\r" in text:
        return None, None

    parsed = parse_key_line(text)
    if parsed is None:
        return None, None

    operation, key, raw_value, has_value = parsed
    if operation != "set" or not has_value:
        return None, None

    if change.key and key.lower() != str(change.key).lower():
        # Переименование ключа — уже не замена строки, а структурная
        # правка. In-place её не выражает.
        return None, None

    value, _inside = parse_value(raw_value)
    return key, value
