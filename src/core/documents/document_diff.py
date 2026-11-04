# -*- coding: utf-8 -*-
"""Семантическое сравнение двух LTX-документов.

Результат сравнения — не текстовый diff, а список намерений изменения:
какой ключ, в какой секции, с какого значения на какое, и насколько
безопасно превращать это в патч.

Модуль намеренно отделяет «изменение есть» от «изменение можно безопасно
выразить патчем». Опасные и неоднозначные случаи попадают в ``issues``,
а не молча превращаются в запись.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .document_model import LtxDocument, LtxSectionModel, SectionView


# ---------------------------------------------------------------- модели


@dataclass(frozen=True)
class SectionSnapshot:
    """Снимок секции для создания новой секции патчем."""

    name: str
    parents: Tuple[str, ...] = ()
    values: Tuple[Tuple[str, Optional[str]], ...] = ()
    list_ops: Tuple[Tuple[str, Tuple[Tuple[str, Optional[str]], ...]], ...] = ()


@dataclass(frozen=True)
class DocumentChange:
    """Одно семантическое изменение."""

    kind: str
    section: str
    key: Optional[str] = None
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    parent: Optional[str] = None
    risk: str = "safe"          # safe | confirm | unsupported
    line_before: int = 0
    line_after: int = 0
    note: str = ""
    section_snapshot: Optional[SectionSnapshot] = None

    @property
    def is_change(self) -> bool:
        return self.kind not in ("noop", "unchanged")


@dataclass(frozen=True)
class DiffIssue:
    """Проблема, обнаруженная при сравнении документов."""

    code: str
    message: str
    severity: str = "warning"   # warning | error
    section: Optional[str] = None
    key: Optional[str] = None


@dataclass(frozen=True)
class DocumentDiff:
    """Результат сравнения."""

    before: LtxDocument
    after: LtxDocument
    changes: Tuple[DocumentChange, ...] = ()
    issues: Tuple[DiffIssue, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.changes

    @property
    def has_blocking_issues(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)

    def blocking_issues(self) -> Tuple[DiffIssue, ...]:
        return tuple(
            issue for issue in self.issues if issue.severity == "error"
        )

    def warnings(self) -> Tuple[DiffIssue, ...]:
        return tuple(
            issue for issue in self.issues if issue.severity == "warning"
        )


# ---------------------------------------------------------------- helpers


def _snapshot(section: LtxSectionModel) -> SectionSnapshot:
    view = section.view()
    return SectionSnapshot(
        name=section.name,
        parents=section.effective_parents(),
        values=tuple(sorted(view.present.items())),
        list_ops=tuple(
            (key, tuple(ops))
            for key, ops in sorted(view.list_ops.items())
        ),
    )


def _section_views(document: LtxDocument) -> Dict[str, SectionView]:
    return {
        name: section.view()
        for name, section in document.sections.items()
    }


def _values_differ(old: Optional[str], new: Optional[str]) -> bool:
    return old != new


def _list_ops_equal(left, right) -> bool:
    return tuple(left) == tuple(right)


# ---------------------------------------------------------------- diff


def diff_documents(before: LtxDocument, after: LtxDocument) -> DocumentDiff:
    """Сравнить два документа и вернуть семантические изменения."""
    changes: List[DocumentChange] = []
    issues: List[DiffIssue] = []

    if tuple(before.includes) != tuple(after.includes):
        issues.append(
            DiffIssue(
                code="include_changed",
                message=(
                    "Изменение директив #include не выражается DLTX-патчем "
                    "без изменения структуры загрузки"
                ),
                severity="error",
            )
        )

    before_names = set(before.sections)
    after_names = set(after.sections)

    removed = before_names - after_names
    added = after_names - before_names
    common = before_names & after_names

    for name in sorted(removed):
        explicit = name in after.deleted_sections
        changes.append(
            DocumentChange(
                kind="delete_section",
                section=name,
                risk="confirm",
                note=(
                    "Секция удалена в черновике"
                    if not explicit
                    else "Секция явно помечена !![%s]" % name
                ),
            )
        )
        if not explicit:
            issues.append(
                DiffIssue(
                    code="section_removed",
                    message=(
                        "Секция [%s] исчезла из черновика. Удаление секции "
                        "патчем затронет итоговую конфигурацию целиком, "
                        "а не только этот файл" % name
                    ),
                    severity="warning",
                    section=name,
                )
            )

    for name in sorted(added):
        section = after.sections[name]
        changes.append(
            DocumentChange(
                kind="add_section",
                section=name,
                risk="safe",
                line_after=section.line_number,
                section_snapshot=_snapshot(section),
            )
        )

    for name in sorted(common):
        before_section = before.sections[name]
        after_section = after.sections[name]
        before_view = before_section.view()
        after_view = after_section.view()

        before_parents = before_section.effective_parents()
        after_parents = after_section.effective_parents()

        for parent in before_parents:
            if parent not in after_parents:
                changes.append(
                    DocumentChange(
                        kind="remove_parent",
                        section=name,
                        parent=parent,
                        risk="confirm",
                        line_before=before_section.line_number,
                        line_after=after_section.line_number,
                    )
                )

        for parent in after_parents:
            if parent not in before_parents:
                changes.append(
                    DocumentChange(
                        kind="add_parent",
                        section=name,
                        parent=parent,
                        risk="safe",
                        line_before=before_section.line_number,
                        line_after=after_section.line_number,
                    )
                )

        before_keys = set(before_view.present)
        after_keys = set(after_view.present)

        for key in sorted(after_keys - before_keys):
            changes.append(
                DocumentChange(
                    kind="add_key",
                    section=name,
                    key=key,
                    new_value=after_view.present[key],
                    risk="safe",
                    line_after=_entry_line(after_section, key),
                )
            )

        for key in sorted(before_keys - after_keys):
            changes.append(
                DocumentChange(
                    kind="delete_key",
                    section=name,
                    key=key,
                    old_value=before_view.present[key],
                    risk="confirm",
                    line_before=_entry_line(before_section, key),
                )
            )

        for key in sorted(before_keys & after_keys):
            old_value = before_view.present[key]
            new_value = after_view.present[key]
            if _values_differ(old_value, new_value):
                changes.append(
                    DocumentChange(
                        kind="set_key",
                        section=name,
                        key=key,
                        old_value=old_value,
                        new_value=new_value,
                        risk="safe",
                        line_before=_entry_line(before_section, key),
                        line_after=_entry_line(after_section, key),
                    )
                )

        # Ключи, которые есть только как операции над списком.
        before_op_keys = set(before_view.list_ops)
        after_op_keys = set(after_view.list_ops)

        for key in sorted(before_op_keys | after_op_keys):
            old_ops = before_view.list_ops.get(key, ())
            new_ops = after_view.list_ops.get(key, ())

            if _list_ops_equal(old_ops, new_ops):
                continue

            old_set = set(old_ops)
            new_set = set(new_ops)

            for op in new_ops:
                if op in old_set:
                    continue
                kind = "list_append" if op[0] == "append" else "list_remove"
                changes.append(
                    DocumentChange(
                        kind=kind,
                        section=name,
                        key=key,
                        new_value=op[1],
                        risk="safe",
                        line_after=_entry_line(after_section, key),
                        note="Добавлена операция над списком",
                    )
                )

            removed_ops = [op for op in old_ops if op not in new_set]
            if removed_ops:
                issues.append(
                    DiffIssue(
                        code="list_operation_removed",
                        message=(
                            "В секции [%s] удалена операция над списком "
                            "'%s'. Удаление уже применённой операции нельзя "
                            "выразить патчем без пересчёта итогового списка"
                            % (name, key)
                        ),
                        severity="error",
                        section=name,
                        key=key,
                    )
                )

    # Диагностика парсера относится к черновику: она важна для плана.
    for message in before.diagnostics:
        issues.append(
            DiffIssue(
                code="before_parse",
                message=message,
                severity="warning",
            )
        )

    for message in after.diagnostics:
        issues.append(
            DiffIssue(
                code="after_parse",
                message=message,
                severity="warning",
            )
        )

    return DocumentDiff(
        before=before,
        after=after,
        changes=tuple(changes),
        issues=tuple(issues),
    )


def diff_texts(
    before_text: str,
    after_text: str,
    source_path=None,
) -> DocumentDiff:
    """Сравнить два текста без чтения файлов."""
    from .document_model import parse_ltx_document

    return diff_documents(
        parse_ltx_document(before_text, source_path=source_path),
        parse_ltx_document(after_text, source_path=source_path),
    )


# ---------------------------------------------------------------- helpers


def _entry_line(section: LtxSectionModel, key: str) -> int:
    line = 0
    for entry in section.entries:
        if entry.key == key:
            line = entry.line_number
    return line



