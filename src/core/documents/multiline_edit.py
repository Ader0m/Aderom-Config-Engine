# -*- coding: utf-8 -*-
"""Многострочная правка resolved-текста.

Модуль сравнивает два полноценных документа и строит один план изменений
для одного файла. Он поддерживает:

- несколько изменённых строк;
- добавление и удаление ключей;
- добавление и удаление блоков секций;
- переименование секции (как удаление старой + создание новой);
- смену наследования;
- конфликты между строками и их разрешение по итоговому тексту.

Итоговый текст считается источником истины: если одна и та же строка
встречается несколько раз, побеждает последнее значение — ровно так же,
как это делает движок.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .document_diff import DocumentChange, DiffIssue, diff_documents
from .document_model import LtxDocument, parse_ltx_document
from .resolved_edit import line_context


@dataclass(frozen=True)
class EditConflict:
    """Конфликт, обнаруженный между изменениями строк."""

    code: str
    message: str
    severity: str = "warning"   # warning | error
    section: Optional[str] = None
    key: Optional[str] = None
    resolved: bool = False


@dataclass(frozen=True)
class MultiLineEditAnalysis:
    """Результат многострочного сравнения."""

    before: Optional[LtxDocument] = None
    after: Optional[LtxDocument] = None
    changes: Tuple[DocumentChange, ...] = ()
    issues: Tuple[DiffIssue, ...] = ()
    conflicts: Tuple[EditConflict, ...] = ()
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


KEY_KINDS = frozenset(
    {"add_key", "set_key", "delete_key", "list_append", "list_remove"}
)
PARENT_KINDS = frozenset({"add_parent", "remove_parent"})


def _changed_line_count(before: str, after: str) -> int:
    old_lines = str(before or "").replace("\r\n", "\n").split("\n")
    new_lines = str(after or "").replace("\r\n", "\n").split("\n")
    matcher = difflib.SequenceMatcher(
        None,
        old_lines,
        new_lines,
        autojunk=False,
    )
    count = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            count += max(i2 - i1, j2 - j1)
    return count


def _section_values(document: LtxDocument, section: str):
    view = document.section_view(section)
    return tuple(sorted(view.present.items()))


def _detect_conflicts(
    changes: Sequence[DocumentChange],
    before: LtxDocument,
    after: LtxDocument,
) -> Tuple[List[DocumentChange], List[EditConflict], List[str]]:
    """Разрешить противоречивые изменения по итоговому документу."""
    conflicts: List[EditConflict] = []
    notes: List[str] = []

    key_groups: Dict[Tuple[str, str], List[DocumentChange]] = {}
    section_groups: Dict[str, List[DocumentChange]] = {}
    parent_groups: Dict[Tuple[str, str], List[DocumentChange]] = {}

    for change in changes:
        if change.kind in KEY_KINDS and change.key:
            key_groups.setdefault(
                (change.section, change.key),
                [],
            ).append(change)
        elif change.kind in ("add_section", "delete_section"):
            section_groups.setdefault(change.section, []).append(change)
        elif change.kind in PARENT_KINDS and change.parent:
            parent_groups.setdefault(
                (change.section, change.parent),
                [],
            ).append(change)

    resolved: List[DocumentChange] = []

    for (section, key), group in key_groups.items():
        if len(group) <= 1:
            resolved.extend(group)
            continue

        conflict = EditConflict(
            code="key_changed_many_times",
            message=(
                "Ключ '%s' в [%s] изменён несколькими строками; "
                "применяется итоговое значение" % (key, section)
            ),
            section=section,
            key=key,
            resolved=True,
        )
        conflicts.append(conflict)

        view = after.section_view(section)

        if key in view.present:
            resolved.append(
                DocumentChange(
                    kind="set_key",
                    section=section,
                    key=key,
                    new_value=view.present[key],
                    risk="safe",
                    note="Конфликт разрешён по итоговому значению",
                )
            )

        for operation, value in view.list_ops.get(key, ()):
            resolved.append(
                DocumentChange(
                    kind=(
                        "list_append"
                        if operation == "append"
                        else "list_remove"
                    ),
                    section=section,
                    key=key,
                    new_value=value,
                    risk="safe",
                    note="Конфликт разрешён по итоговой операции списка",
                )
            )

        if key not in view.present and not view.list_ops.get(key):
            resolved.append(
                DocumentChange(
                    kind="delete_key",
                    section=section,
                    key=key,
                    risk="confirm",
                    note="Конфликт разрешён: ключ отсутствует в итоге",
                )
            )

        notes.append(
            "Конфликт [%s] %s: выбрано итоговое значение" % (section, key)
        )

    for section, group in section_groups.items():
        kinds = {change.kind for change in group}
        if len(kinds) <= 1:
            resolved.extend(group)
            continue

        conflict = EditConflict(
            code="section_changed_many_times",
            message=(
                "Секция [%s] одновременно создаётся и удаляется; "
                "применяется итоговое состояние" % section
            ),
            section=section,
            resolved=True,
        )
        conflicts.append(conflict)

        if section in after.sections:
            resolved.extend(
                change for change in group
                if change.kind == "add_section"
            )
        else:
            resolved.append(
                DocumentChange(
                    kind="delete_section",
                    section=section,
                    risk="confirm",
                )
            )

        notes.append(
            "Конфликт секции [%s]: выбрано итоговое состояние" % section
        )

    for (section, parent), group in parent_groups.items():
        kinds = {change.kind for change in group}
        if len(kinds) <= 1:
            resolved.extend(group)
            continue

        conflict = EditConflict(
            code="parent_changed_many_times",
            message=(
                "Родитель '%s' секции [%s] одновременно добавляется и "
                "удаляется; применяется итоговое наследование"
                % (parent, section)
            ),
            section=section,
            resolved=True,
        )
        conflicts.append(conflict)

        if parent in after.parents(section):
            resolved.append(
                DocumentChange(
                    kind="add_parent",
                    section=section,
                    parent=parent,
                    risk="safe",
                )
            )
        else:
            resolved.append(
                DocumentChange(
                    kind="remove_parent",
                    section=section,
                    parent=parent,
                    risk="confirm",
                )
            )

        notes.append(
            "Конфликт родителя [%s] %s: выбрано итоговое наследование"
            % (section, parent)
        )

    # Изменения, не попавшие в группы.
    grouped_ids = {
        id(change)
        for group in key_groups.values()
        for change in group
    }
    grouped_ids.update(
        id(change)
        for group in section_groups.values()
        for change in group
    )
    grouped_ids.update(
        id(change)
        for group in parent_groups.values()
        for change in group
    )
    resolved.extend(
        change for change in changes if id(change) not in grouped_ids
    )

    return resolved, conflicts, notes


def _duplicate_key_conflicts(after: LtxDocument) -> Tuple[List[EditConflict], List[str]]:
    conflicts: List[EditConflict] = []
    notes: List[str] = []

    for name, section in after.sections.items():
        seen: Dict[str, int] = {}
        for entry in section.entries:
            if entry.operation != "set":
                continue
            seen[entry.key] = seen.get(entry.key, 0) + 1

        for key, count in seen.items():
            if count <= 1:
                continue
            conflicts.append(
                EditConflict(
                    code="duplicate_key",
                    message=(
                        "Ключ '%s' в [%s] встречается %d раза; "
                        "применяется последнее значение"
                        % (key, name, count)
                    ),
                    section=name,
                    key=key,
                    resolved=True,
                )
            )
            notes.append(
                "Конфликт [%s] %s: несколько строк, выбрано последнее значение"
                % (name, key)
            )

    return conflicts, notes


def _summary_notes(
    changes: Sequence[DocumentChange],
    before: LtxDocument,
    after: LtxDocument,
    changed_lines: int,
) -> List[str]:
    notes: List[str] = []

    if changed_lines:
        notes.append("Изменено строк: %d" % changed_lines)

    by_section: Dict[str, List[DocumentChange]] = {}
    for change in changes:
        by_section.setdefault(change.section, []).append(change)

    for section in sorted(by_section):
        group = by_section[section]
        kinds = [change.kind for change in group]

        if "delete_section" in kinds:
            notes.append("Секция [%s]: удалена" % section)
            continue

        if "add_section" in kinds:
            snapshot = next(
                (
                    change.section_snapshot
                    for change in group
                    if change.kind == "add_section"
                ),
                None,
            )
            added = len(snapshot.values) if snapshot is not None else 0
            notes.append(
                "Секция [%s]: создана, ключей: %d" % (section, added)
            )
            continue

        changed = len(
            [kind for kind in kinds if kind in ("set_key", "add_key")]
        )
        deleted = len([kind for kind in kinds if kind == "delete_key"])
        lists = len(
            [kind for kind in kinds if kind in ("list_append", "list_remove")]
        )
        parents = len([kind for kind in kinds if kind in PARENT_KINDS])

        parts = []
        if changed:
            parts.append("изменено/добавлено ключей: %d" % changed)
        if deleted:
            parts.append("удалено ключей: %d" % deleted)
        if lists:
            parts.append("операций со списками: %d" % lists)
        if parents:
            parts.append("изменений наследования: %d" % parents)

        if parts:
            notes.append("Секция [%s]: %s" % (section, ", ".join(parts)))

    # Возможные переименования секций: удалена старая, добавлена новая
    # с тем же набором значений.
    removed = {
        change.section
        for change in changes
        if change.kind == "delete_section"
    }
    added = {
        change.section
        for change in changes
        if change.kind == "add_section"
    }

    for old in sorted(removed):
        old_values = _section_values(before, old)
        for new in sorted(added):
            if not old_values:
                continue
            if old_values == _section_values(after, new):
                notes.append(
                    "Секция [%s] преобразована в [%s] (переименование)"
                    % (old, new)
                )

    return notes


def _winner_label(source) -> str:
    if source is None:
        return "неизвестен"
    display = getattr(source, "display", "") or ""
    if display:
        return display
    path = getattr(source, "path", None)
    return str(path) if path is not None else "неизвестен"


def _winner_notes(
    original_text: str,
    edited_text: str,
    line_sources: Sequence = (),
) -> List[str]:
    old_lines = str(original_text or "").replace("\r\n", "\n").split("\n")
    new_lines = str(edited_text or "").replace("\r\n", "\n").split("\n")
    sources = list(line_sources or ())
    sections, keys, _section_lines, _key_lines = line_context(old_lines)

    notes: List[str] = []
    matcher = difflib.SequenceMatcher(
        None,
        old_lines,
        new_lines,
        autojunk=False,
    )

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue

        if tag == "replace" and (i2 - i1) == (j2 - j1):
            for offset in range(i2 - i1):
                i = i1 + offset
                section = sections[i] if i < len(sections) else None
                key = keys[i] if i < len(keys) else None
                source = sources[i] if i < len(sources) else None

                if section and key:
                    notes.append(
                        "Строка %d: [%s] %s — победитель: %s"
                        % (i + 1, section, key, _winner_label(source))
                    )
                else:
                    notes.append(
                        "Строка %d: структурное изменение"
                        % (i + 1)
                    )
        else:
            notes.append(
                "Строки %d–%d: вставка/удаление блока"
                % (i1 + 1, max(i1 + 1, i2))
            )

    return notes


def analyze_multiline_edit(
    original_text: str,
    edited_text: str,
    line_sources: Sequence = (),
) -> MultiLineEditAnalysis:
    """Сравнить два полных текста и подготовить один план."""
    before = parse_ltx_document(original_text)
    after = parse_ltx_document(edited_text)
    diff = diff_documents(before, after)

    resolved_changes, conflicts, conflict_notes = _detect_conflicts(
        diff.changes,
        before,
        after,
    )
    duplicate_conflicts, duplicate_notes = _duplicate_key_conflicts(after)
    conflicts.extend(duplicate_conflicts)
    conflict_notes.extend(duplicate_notes)

    changed_lines = _changed_line_count(original_text, edited_text)
    notes = _summary_notes(
        resolved_changes,
        before,
        after,
        changed_lines,
    )
    notes.extend(_winner_notes(original_text, edited_text, line_sources))
    notes.extend(conflict_notes)

    issues: List[DiffIssue] = list(diff.issues)
    for conflict in conflicts:
        issues.append(
            DiffIssue(
                code="conflict:" + conflict.code,
                message=conflict.message,
                severity=conflict.severity,
                section=conflict.section,
                key=conflict.key,
            )
        )

    return MultiLineEditAnalysis(
        before=before,
        after=after,
        changes=tuple(resolved_changes),
        issues=tuple(issues),
        conflicts=tuple(conflicts),
        notes=tuple(notes),
    )
