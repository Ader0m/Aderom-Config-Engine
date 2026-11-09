# -*- coding: utf-8 -*-
"""Модель LTX-документа и отслеживание изменений.

Модуль не является резолвером и не подменяет ``Resolver4.py``. Его задача
уже: представить один физический LTX-файл как структуру секций и ключей,
пригодную для семантического сравнения черновика с оригиналом.

Модель сознательно повторяет наблюдаемые правила движка:

- имена секций приводятся к нижнему регистру;
- поддерживаются ``[section]``, ``![section]``, ``@[section]``,
  ``!![section]`` и наследование ``[section]:p1,p2``;
- поддерживаются ``!key``, ``>key``, ``<key``;
- комментарии ``;`` и ``//`` не учитываются внутри кавычек;
- пустое значение (``key =`` или ``key``) хранится как ``None``;
- многострочные значения собираются по нечётному числу кавычек.

Модель не пытается применять include и не вычисляет финальное значение
секции с учётом других файлов. Это задача резолвера и семантического слоя.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..fs import read_text_safe, strip_comments


# ---------------------------------------------------------------- значения


def _parse_value_partial(
    raw: str,
    inside: bool = False,
) -> Tuple[str, bool]:
    """Разобрать фрагмент значения, начав в состоянии ``inside``.

    Отдельная функция нужна для многострочных значений: раньше при
    добавлении каждой строки заново разбиралось всё накопленное значение,
    что давало O(n²) на длинных блоках. Теперь каждая строка разбирается
    один раз, а состояние кавычек переносится.
    """
    # Быстрые пути без посимвольного разбора.
    if '"' not in raw:
        if inside:
            # Внутри кавычек пробелы сохраняются как есть.
            return raw, True
        # Вне кавычек пробелы удаляются полностью.
        return "".join(raw.split()), False

    out: List[str] = []
    i = 0
    n = len(raw)

    while i < n:
        ch = raw[i]

        if ch.isspace():
            if inside:
                out.append(ch)
                i += 1
                continue

            while i < n and raw[i].isspace():
                i += 1
            if i >= n:
                break
            ch = raw[i]

        if ch == '"':
            inside = not inside

        out.append(ch)
        i += 1

    return "".join(out), inside


def _parse_value(raw: str) -> Tuple[Optional[str], bool]:
    """Разобрать значение так, как это делает ``CInifile::_parse``.

    Возвращает ``(value, inside_quotes)``. Пробелы вне кавычек удаляются,
    пробелы внутри кавычек сохраняются. Кавычки остаются частью значения,
    потому что именно так их хранит ``Resolver4``.
    """
    text, inside = _parse_value_partial(raw, False)
    return (text if text else None), inside


def _split_csv(text: str) -> List[str]:
    """Разделить список по запятым вне кавычек."""
    parts: List[str] = []
    current: List[str] = []
    inside = False

    for ch in text:
        if ch == '"':
            inside = not inside
            current.append(ch)
        elif ch == "," and not inside:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)

    parts.append("".join(current).strip())
    return [p for p in parts if p]


def _find_unquoted(text: str, needle: str) -> int:
    """Позиция символа вне кавычек или -1."""
    if '"' not in text:
        return text.find(needle)

    inside = False
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]
        if ch == '"':
            inside = not inside
        elif not inside and ch == needle:
            return i
        i += 1

    return -1


# ---------------------------------------------------------------- структуры


@dataclass(frozen=True)
class LtxEntry:
    """Одна значимая строка внутри секции."""

    key: str
    value: Optional[str]
    operation: str          # set | delete | append | remove
    line_number: int
    raw: str = ""
    multiline: bool = False


@dataclass(frozen=True)
class SectionView:
    """Эффективное представление секции внутри одного документа."""

    present: Dict[str, Optional[str]] = field(default_factory=dict)
    deleted: frozenset = field(default_factory=frozenset)
    list_ops: Dict[str, Tuple[Tuple[str, Optional[str]], ...]] = field(
        default_factory=dict
    )

    @property
    def is_empty(self) -> bool:
        return not self.present and not self.deleted and not self.list_ops


@dataclass
class LtxSectionModel:
    """Секция одного документа.

    ``parents`` хранит токены наследования в исходном порядке, включая
    удаления вида ``!parent``. Эффективный набор родителей вычисляется
    методом :meth:`effective_parents`.
    """

    name: str
    header_kind: str        # base | override | safe | delete
    parents: List[str] = field(default_factory=list)
    entries: List[LtxEntry] = field(default_factory=list)
    line_number: int = 0

    def effective_parents(self) -> Tuple[str, ...]:
        out: List[str] = []

        for token in self.parents:
            if token.startswith("!"):
                target = token[1:]
                out = [p for p in out if p != target]
            elif token not in out:
                out.append(token)

        return tuple(out)

    def view(self) -> SectionView:
        present: Dict[str, Optional[str]] = {}
        deleted = set()
        list_ops: Dict[str, List[Tuple[str, Optional[str]]]] = {}

        for entry in self.entries:
            if entry.operation == "set":
                present[entry.key] = entry.value
                deleted.discard(entry.key)
            elif entry.operation == "delete":
                present.pop(entry.key, None)
                deleted.add(entry.key)
            elif entry.operation in ("append", "remove"):
                list_ops.setdefault(entry.key, []).append(
                    (entry.operation, entry.value)
                )

        return SectionView(
            present=dict(present),
            deleted=frozenset(deleted),
            list_ops={k: tuple(v) for k, v in list_ops.items()},
        )


@dataclass
class LtxDocument:
    """Разобранный физический LTX-документ."""

    source_path: Optional[Path]
    text: str
    sections: Dict[str, LtxSectionModel] = field(default_factory=dict)
    deleted_sections: set = field(default_factory=set)
    includes: Tuple[str, ...] = ()
    diagnostics: Tuple[str, ...] = ()

    def section(self, name: str) -> Optional[LtxSectionModel]:
        return self.sections.get((name or "").lower())

    def section_view(self, name: str) -> SectionView:
        section = self.section(name)
        return section.view() if section is not None else SectionView()

    def values(self, name: str) -> Dict[str, Optional[str]]:
        return dict(self.section_view(name).present)

    def parents(self, name: str) -> Tuple[str, ...]:
        section = self.section(name)
        return section.effective_parents() if section is not None else ()

    def defined_names(self) -> set:
        return set(self.sections)

    @property
    def is_empty(self) -> bool:
        return not self.sections and not self.deleted_sections


# ---------------------------------------------------------------- парсер


def _parse_section_header(line: str):
    """Вернуть ``(kind, name, parents, rest)`` или ``None``."""
    if line.startswith("!!["):
        close = line.find("]")
        if close == -1:
            return None
        return ("delete", line[3:close].strip().lower(), [], "")

    candidates = (
        ("![", "override"),
        ("@[", "safe"),
        ("[", "base"),
    )

    for prefix, kind in candidates:
        if not line.startswith(prefix):
            continue

        close = line.find("]", len(prefix))
        if close == -1:
            return None

        name = line[len(prefix):close].strip().lower()
        rest = line[close + 1:]

        parents: List[str] = []
        if rest.startswith(":"):
            for token in _split_csv(rest[1:]):
                token = token.strip().lower()
                if token:
                    parents.append(token)

        return (kind, name, parents, rest)

    return None


def _parse_key_line(line: str):
    """Вернуть ``(operation, key, raw_value, has_value)`` или ``None``."""
    if not line:
        return None

    if (
        line.startswith("![")
        or line.startswith("!![")
        or line.startswith("@[")
    ):
        return None

    operation = "set"
    body = line

    if line.startswith("!"):
        operation = "delete"
        body = line[1:].lstrip()
    elif line.startswith(">"):
        operation = "append"
        body = line[1:].lstrip()
    elif line.startswith("<"):
        operation = "remove"
        body = line[1:].lstrip()

    eq = _find_unquoted(body, "=")

    if eq == -1:
        key = body.strip()
        return (operation, key, "", False) if key else None

    key = body[:eq].strip()
    if not key:
        return None

    return (operation, key, body[eq + 1:], True)


def parse_section_header(line: str):
    """Публичная обёртка разбора заголовка секции."""
    return _parse_section_header(line)


def parse_key_line(line: str):
    """Публичная обёртка разбора строки ключа."""
    return _parse_key_line(line)


def parse_value(raw: str):
    """Публичная обёртка разбора значения (с флагом незакрытой кавычки)."""
    return _parse_value(raw)


def split_csv(text: str):
    """Публичная обёртка разбора CSV-списка."""
    return _split_csv(text)


def parse_ltx_document(text: str, source_path=None) -> LtxDocument:
    """Разобрать LTX-текст в :class:`LtxDocument`."""
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")

    sections: Dict[str, LtxSectionModel] = {}
    deleted_sections = set()
    includes: List[str] = []
    diagnostics: List[str] = []

    current: Optional[LtxSectionModel] = None
    i = 0

    while i < len(lines):
        raw_line = lines[i]
        line_number = i + 1
        line = strip_comments(raw_line).strip()

        if not line:
            i += 1
            continue

        if line.startswith("#"):
            includes.append(line)
            i += 1
            continue

        header = _parse_section_header(line)

        if header is not None:
            kind, name, parents, _rest = header

            if not name:
                diagnostics.append(
                    "Строка %d: пустое имя секции" % line_number
                )
                current = None
                i += 1
                continue

            if kind == "delete":
                deleted_sections.add(name)
                sections.pop(name, None)
                current = None
                i += 1
                continue

            if name in deleted_sections:
                diagnostics.append(
                    "Строка %d: секция [%s] определяется после !![%s]"
                    % (line_number, name, name)
                )

            block = LtxSectionModel(
                name=name,
                header_kind=kind,
                parents=list(parents),
                line_number=line_number,
            )

            existing = sections.get(name)
            if existing is None:
                sections[name] = block
            else:
                if existing.header_kind == "base" and kind == "base":
                    diagnostics.append(
                        "Строка %d: повторное объявление [%s] без ! "
                        "будет ошибкой движка"
                        % (line_number, name)
                    )
                existing.parents.extend(block.parents)
                existing.entries.extend(block.entries)

            current = sections.get(name)
            i += 1
            continue

        parsed_key = _parse_key_line(line)

        if parsed_key is None:
            diagnostics.append(
                "Строка %d: нераспознанная конструкция: %s"
                % (line_number, line)
            )
            i += 1
            continue

        operation, key, raw_value, has_value = parsed_key

        if current is None:
            diagnostics.append(
                "Строка %d: ключ '%s' вне секции" % (line_number, key)
            )
            i += 1
            continue

        value = None
        multiline = False

        if has_value and operation != "delete":
            value, inside = _parse_value(raw_value)
            while inside and i + 1 < len(lines):
                i += 1
                # Продолжение многострочного значения разбираем только
                # для новой строки, не переразбирая всё накопленное.
                piece, inside = _parse_value_partial(lines[i], True)
                value = (value or "") + "\n" + piece
                multiline = True

            if inside:
                diagnostics.append(
                    "Строка %d: незакрытая кавычка в секции [%s]"
                    % (line_number, current.name)
                )

        current.entries.append(
            LtxEntry(
                key=key,
                value=value if operation != "delete" else None,
                operation=operation,
                line_number=line_number,
                raw=line,
                multiline=multiline,
            )
        )
        i += 1

    return LtxDocument(
        source_path=Path(source_path) if source_path is not None else None,
        text=normalized,
        sections=sections,
        deleted_sections=deleted_sections,
        includes=tuple(includes),
        diagnostics=tuple(diagnostics),
    )


def parse_ltx_file(path) -> LtxDocument:
    """Прочитать и разобрать LTX-файл."""
    path = Path(path)
    return parse_ltx_document(read_text_safe(path), source_path=path)
