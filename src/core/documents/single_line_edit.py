# -*- coding: utf-8 -*-
"""Однострочная правка resolved-текста и полный набор DLTX-инструкций.

Этап обучения: разрешено менять ровно одну строку. Даже в этом режиме
поддерживаются все основные виды правок:

- значение ключа;
- переименование ключа (удаление старого + установка нового);
- удаление ключа;
- добавление ключа на месте пустой/комментированной строки;
- операции над списком ``>key`` / ``<key``;
- добавление/удаление родителя секции;
- удаление секции через ``!![section]``;
- создание пустой секции в конце файла через ``@[section]``.

Структурные правки, меняющие смысл соседних строк (переименование секции,
вставка секции перед ключами, замена заголовка на ключ и наоборот),
блокируются: для них нужен многострочный план.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .document_diff import DocumentChange, DiffIssue, SectionSnapshot
from .document_model import (
    parse_key_line,
    parse_section_header,
    parse_value,
)
from ..fs import strip_comments
from .resolved_edit import line_context


@dataclass(frozen=True)
class SingleLineEditAnalysis:
    """Разбор ровно одной изменённой строки."""

    line_number: int = 0
    old_line: str = ""
    new_line: str = ""
    section: Optional[str] = None
    changes: Tuple[DocumentChange, ...] = ()
    issues: Tuple[DiffIssue, ...] = ()
    notes: Tuple[str, ...] = ()

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)

    @property
    def is_supported(self) -> bool:
        return bool(self.changes) and not self.has_errors

    @property
    def is_empty(self) -> bool:
        return not self.changes and not self.has_errors


def _section_view(tokens: Sequence[str]) -> Tuple[str, ...]:
    out: List[str] = []
    for token in tokens:
        if token.startswith("!"):
            target = token[1:]
            out = [parent for parent in out if parent != target]
        elif token not in out:
            out.append(token)
    return tuple(out)


def _value_of(parsed) -> Optional[str]:
    if parsed is None:
        return None
    operation, _key, raw_value, has_value = parsed
    if operation == "delete" or not has_value:
        return None
    return parse_value(raw_value)[0]


def _kind_of(clean: str) -> str:
    if not clean:
        return "blank"
    if clean.startswith("#"):
        return "include"
    if parse_section_header(clean) is not None:
        return "section"
    if parse_key_line(clean) is not None:
        return "key"
    return "comment"


def _winner_label(source) -> str:
    if source is None:
        return "неизвестен"
    display = getattr(source, "display", "") or ""
    if display:
        value = display
    else:
        path = getattr(source, "path", None)
        if path is None:
            return "неизвестен"
        value = str(path)
    value = value.replace("\\", "/").rstrip("/")
    return value.rsplit("/", 1)[-1] or value


def _only_blank_after(lines: Sequence[str], index: int) -> bool:
    for line in lines[index + 1:]:
        clean = strip_comments(line).strip()
        if clean:
            return False
    return True


def analyze_single_line_edit(
    original_text: str,
    edited_text: str,
    line_sources: Sequence = (),
) -> SingleLineEditAnalysis:
    """Разобрать ровно одну изменённую строку resolved-текста."""
    old_lines = str(original_text or "").replace("\r\n", "\n").split("\n")
    new_lines = str(edited_text or "").replace("\r\n", "\n").split("\n")

    if len(old_lines) != len(new_lines):
        return SingleLineEditAnalysis(
            issues=(
                DiffIssue(
                    code="line_count_changed",
                    message=(
                        "Этап поддерживает ровно одну изменённую строку "
                        "без вставки и удаления строк"
                    ),
                    severity="error",
                ),
            ),
        )

    changed = [
        index for index, (old, new) in enumerate(zip(old_lines, new_lines))
        if old != new
    ]

    if not changed:
        return SingleLineEditAnalysis(
            issues=(
                DiffIssue(
                    code="no_changes",
                    message="Строка не изменена",
                    severity="warning",
                ),
            ),
        )

    if len(changed) > 1:
        return SingleLineEditAnalysis(
            issues=(
                DiffIssue(
                    code="multiple_lines_changed",
                    message=(
                        "Этап поддерживает ровно одну изменённую строку; "
                        "изменено строк: %d" % len(changed)
                    ),
                    severity="error",
                ),
            ),
        )

    index = changed[0]
    old_line = old_lines[index]
    new_line = new_lines[index]
    old_clean = strip_comments(old_line).strip()
    new_clean = strip_comments(new_line).strip()

    old_kind = _kind_of(old_clean)
    new_kind = _kind_of(new_clean)

    sections, _keys, _section_lines, _key_lines = line_context(old_lines)
    section = sections[index] if index < len(sections) else None

    old_header = parse_section_header(old_clean)
    new_header = parse_section_header(new_clean)
    old_key = parse_key_line(old_clean)
    new_key = parse_key_line(new_clean)

    changes: List[DocumentChange] = []
    issues: List[DiffIssue] = []
    notes: List[str] = []
    winner = _winner_label(
        line_sources[index] if index < len(line_sources) else None
    )

    def add(change: DocumentChange):
        changes.append(change)

    def error(code: str, message: str):
        issues.append(
            DiffIssue(
                code=code,
                message=message,
                severity="error",
                section=section,
            )
        )

    if old_kind == "include" or new_kind == "include":
        error(
            "include_changed",
            "Изменение #include не выражается однострочным DLTX-патчем",
        )

    elif old_kind == "section" and new_kind == "section":
        old_name = old_header[1]
        new_name = new_header[1]
        old_parents = _section_view(old_header[2])
        new_parents = _section_view(new_header[2])

        if old_name != new_name:
            error(
                "section_renamed",
                "Переименование секции меняет структуру и требует "
                "многострочного плана",
            )
        elif new_header[0] == "delete":
            add(
                DocumentChange(
                    kind="delete_section",
                    section=old_name,
                    risk="confirm",
                    line_before=index + 1,
                    line_after=index + 1,
                    note="Секция помечена на удаление",
                )
            )
        else:
            for parent in old_parents:
                if parent not in new_parents:
                    add(
                        DocumentChange(
                            kind="remove_parent",
                            section=old_name,
                            parent=parent,
                            risk="confirm",
                            line_before=index + 1,
                            line_after=index + 1,
                        )
                    )
            for parent in new_parents:
                if parent not in old_parents:
                    add(
                        DocumentChange(
                            kind="add_parent",
                            section=old_name,
                            parent=parent,
                            risk="safe",
                            line_before=index + 1,
                            line_after=index + 1,
                        )
                    )

            if old_header[0] != new_header[0] and not changes:
                notes.append(
                    "Строка %d: маркер заголовка [%s] изменён, "
                    "но итоговый набор родителей не изменился"
                    % (index + 1, old_name)
                )

        if changes:
            notes.append(
                "Строка %d: секция [%s], победитель: %s"
                % (index + 1, old_name, winner)
            )

    elif old_kind == "key" and new_kind == "key":
        if not section:
            error("no_section", "Не удалось определить секцию для ключа")
        else:
            old_op, old_key_name, _old_raw, _old_has = old_key
            new_op, new_key_name, _new_raw, _new_has = new_key
            old_value = _value_of(old_key)
            new_value = _value_of(new_key)

            if old_op in ("append", "remove"):
                if (
                    old_op == new_op
                    and old_key_name == new_key_name
                    and old_value == new_value
                ):
                    notes.append(
                        "Строка %d: операция над списком не изменилась"
                        % (index + 1)
                    )
                else:
                    error(
                        "list_operation_changed",
                        "Изменение уже существующей операции >/< "
                        "не выражается однострочным патчем",
                    )
            elif new_op == "delete":
                if new_key_name == old_key_name:
                    add(
                        DocumentChange(
                            kind="delete_key",
                            section=section,
                            key=old_key_name,
                            old_value=old_value,
                            risk="confirm",
                            line_before=index + 1,
                            line_after=index + 1,
                        )
                    )
                else:
                    error(
                        "rename_with_delete",
                        "Переименование ключа с одновременным удалением "
                        "не поддержано",
                    )
            elif new_op in ("append", "remove"):
                error(
                    "value_to_list_operation",
                    "Замена значения на операцию >/< не создаёт итогового "
                    "значения; измените сам список или добавьте операцию "
                    "на месте пустой строки",
                )
            else:
                if new_key_name == old_key_name:
                    if old_value != new_value:
                        add(
                            DocumentChange(
                                kind="set_key",
                                section=section,
                                key=old_key_name,
                                old_value=old_value,
                                new_value=new_value,
                                risk="safe",
                                line_before=index + 1,
                                line_after=index + 1,
                            )
                        )
                else:
                    add(
                        DocumentChange(
                            kind="delete_key",
                            section=section,
                            key=old_key_name,
                            old_value=old_value,
                            risk="confirm",
                            line_before=index + 1,
                            line_after=index + 1,
                            note="Переименование ключа: удаление старого",
                        )
                    )
                    add(
                        DocumentChange(
                            kind="add_key",
                            section=section,
                            key=new_key_name,
                            new_value=new_value,
                            risk="safe",
                            line_before=index + 1,
                            line_after=index + 1,
                            note="Переименование ключа: добавление нового",
                        )
                    )

            if changes:
                notes.append(
                    "Строка %d: [%s] %s, победитель: %s"
                    % (index + 1, section, old_key_name, winner)
                )

    elif old_kind in ("blank", "comment") and new_kind == "key":
        if not section:
            error(
                "no_section",
                "Не удалось определить секцию для нового ключа",
            )
        else:
            new_op, new_key_name, _new_raw, _new_has = new_key
            new_value = _value_of(new_key)

            if new_op == "delete":
                error(
                    "delete_missing_key",
                    "Удаление ключа на месте пустой строки не имеет смысла",
                )
            elif new_op in ("append", "remove"):
                kind = (
                    "list_append" if new_op == "append" else "list_remove"
                )
                add(
                    DocumentChange(
                        kind=kind,
                        section=section,
                        key=new_key_name,
                        new_value=new_value,
                        risk="safe",
                        line_before=index + 1,
                        line_after=index + 1,
                        note="Операция над списком",
                    )
                )
            else:
                add(
                    DocumentChange(
                        kind="add_key",
                        section=section,
                        key=new_key_name,
                        new_value=new_value,
                        risk="safe",
                        line_before=index + 1,
                        line_after=index + 1,
                    )
                )

            if changes:
                notes.append(
                    "Строка %d: [%s] добавлен ключ %s"
                    % (index + 1, section, new_key_name)
                )

    elif old_kind == "key" and new_kind in ("blank", "comment"):
        if not section:
            error("no_section", "Не удалось определить секцию для ключа")
        else:
            old_op, old_key_name, _old_raw, _old_has = old_key
            add(
                DocumentChange(
                    kind="delete_key",
                    section=section,
                    key=old_key_name,
                    old_value=_value_of(old_key),
                    risk="confirm",
                    line_before=index + 1,
                    line_after=index + 1,
                )
            )
            notes.append(
                "Строка %d: [%s] удалён ключ %s"
                % (index + 1, section, old_key_name)
            )

    elif old_kind in ("blank", "comment") and new_kind == "section":
        if new_header[0] == "delete":
            error(
                "delete_missing_section",
                "Удаление секции на месте пустой строки не поддержано",
            )
        elif not _only_blank_after(old_lines, index):
            error(
                "section_insertion_structural",
                "Вставка заголовка перед ключами меняет структуру секций; "
                "нужен многострочный план",
            )
        else:
            name = new_header[1]
            parents = _section_view(new_header[2])
            add(
                DocumentChange(
                    kind="add_section",
                    section=name,
                    risk="safe",
                    line_before=index + 1,
                    line_after=index + 1,
                    section_snapshot=SectionSnapshot(
                        name=name,
                        parents=parents,
                    ),
                )
            )
            notes.append(
                "Строка %d: создаётся пустая секция [%s]"
                % (index + 1, name)
            )

    elif old_kind in ("blank", "comment") and new_kind in (
        "blank",
        "comment",
    ):
        notes.append(
            "Строка %d: комментарий/пустая строка не влияет на конфигурацию"
            % (index + 1)
        )

    else:
        error(
            "unsupported_line_conversion",
            "Замена '%s' на '%s' меняет структуру и требует "
            "многострочного плана" % (old_kind, new_kind),
        )

    return SingleLineEditAnalysis(
        line_number=index + 1,
        old_line=old_line,
        new_line=new_line,
        section=section,
        changes=tuple(changes),
        issues=tuple(issues),
        notes=tuple(notes),
    )
