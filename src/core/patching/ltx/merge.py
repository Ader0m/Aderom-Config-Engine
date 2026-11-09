# -*- coding: utf-8 -*-
"""Объединение патч-планов разных файлов одного корня.

Режим ``root``: все изменения файлов, относящихся к одному корню
загрузки, собираются в один патч-файл.

Противоречия разрешаются детерминированно: планы упорядочиваются по пути
исходного файла, побеждает последнее изменение. Опасные структурные
конфликты (например, одна секция удаляется и одновременно изменяется)
блокируют генерацию, чтобы не создать заведомо неверный патч.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Dict, List, Optional, Sequence, Tuple

from core.patching.ltx.diff import DocumentChange
from core.patching.ltx.generator import DltxGenerator
from core.patching.models import PatchIssue, PatchPlan


KEY_KINDS = frozenset(
    {"add_key", "set_key", "delete_key", "list_append", "list_remove"}
)
PARENT_KINDS = frozenset({"add_parent", "remove_parent"})


def _plan_order(plan: PatchPlan):
    return str(plan.source_path).lower()


def merge_patch_plans(
    plans: Sequence[PatchPlan],
    patch_mod: Optional[str] = None,
) -> PatchPlan:
    """Слить планы одного корня в один план."""
    valid = [plan for plan in plans if plan.changes]

    if not valid:
        raise ValueError("Нет планов с изменениями")

    if any(getattr(plan, "is_xml", False) for plan in valid):
        raise ValueError(
            "XML-планы объединяются через _merge_xml_generated, а не DLTX"
        )

    roots = {
        str(plan.target.root_path).lower()
        for plan in valid
        if plan.target.root_path is not None
    }
    if len(roots) != 1:
        raise ValueError(
            "Объединять можно только планы одного корня загрузки"
        )

    valid.sort(key=_plan_order)

    issues: List[PatchIssue] = []
    warnings: List[PatchIssue] = []

    for plan in valid:
        issues.extend(plan.issues)
        warnings.extend(plan.warnings)

    key_groups: Dict[Tuple[str, str], List[DocumentChange]] = {}
    section_groups: Dict[str, List[DocumentChange]] = {}
    parent_groups: Dict[Tuple[str, str], List[DocumentChange]] = {}
    others: List[DocumentChange] = []

    for plan in valid:
        for change in plan.changes:
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
            else:
                others.append(change)

    merged: List[DocumentChange] = []

    for (section, key), group in key_groups.items():
        if len(group) > 1:
            warnings.append(
                PatchIssue(
                    code="merge_key_conflict",
                    message=(
                        "Ключ '%s' в [%s] изменён несколькими файлами"
                        % (key, section)
                    ),
                    severity="warning",
                    section=section,
                    key=key,
                )
            )

        if all(
            change.kind in ("list_append", "list_remove")
            for change in group
        ):
            # Операции над списком складываются, а не заменяют друг друга.
            merged.extend(group)
        else:
            merged.append(group[-1])

    deleted_sections = {
        section
        for section, group in section_groups.items()
        if any(change.kind == "delete_section" for change in group)
    }
    added_sections = {
        section
        for section, group in section_groups.items()
        if any(change.kind == "add_section" for change in group)
    }

    for section in sorted(deleted_sections & added_sections):
        issues.append(
            PatchIssue(
                code="merge_section_conflict",
                message=(
                    "Секция [%s] одновременно создаётся и удаляется "
                    "разными файлами" % section
                ),
                severity="error",
                section=section,
            )
        )

    for (section, key), group in key_groups.items():
        if section in deleted_sections:
            issues.append(
                PatchIssue(
                    code="merge_deleted_section_edit",
                    message=(
                        "Секция [%s] удаляется одним файлом и изменяется "
                        "другим" % section
                    ),
                    severity="error",
                    section=section,
                    key=key,
                )
            )

    for section, group in section_groups.items():
        if len(group) > 1 and len({c.kind for c in group}) == 1:
            warnings.append(
                PatchIssue(
                    code="merge_section_duplicate",
                    message=(
                        "Секция [%s] изменена несколькими файлами; "
                        "применено последнее изменение" % section
                    ),
                    severity="warning",
                    section=section,
                )
            )
        merged.append(group[-1])

    for (section, parent), group in parent_groups.items():
        if len(group) > 1:
            warnings.append(
                PatchIssue(
                    code="merge_parent_conflict",
                    message=(
                        "Родитель '%s' секции [%s] изменён несколькими "
                        "файлами; применено последнее изменение"
                        % (parent, section)
                    ),
                    severity="warning",
                    section=section,
                )
            )
        merged.append(group[-1])

    merged.extend(others)

    first = valid[0]
    merged_plan = PatchPlan(
        source_path=first.target.root_path or first.source_path,
        target=first.target,
        patch_mod=patch_mod or first.patch_mod,
        patch_file_rel=first.patch_file_rel,
        changes=tuple(merged),
        issues=tuple(issues),
        warnings=tuple(warnings),
        preview="",
        diff=None,
        snapshot_id=first.snapshot_id,
        encoding=first.encoding,
    )

    if merged_plan.can_generate:
        preview = DltxGenerator().preview(merged_plan)
        merged_plan = replace(merged_plan, preview=preview)

    return merged_plan
