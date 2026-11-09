# -*- coding: utf-8 -*-
"""Планирование LTX/DLTX-патчей."""

from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
from typing import List, Optional

from core.patching.ltx.changes import analyze_single_line_edit
from core.patching.ltx.diff import DocumentDiff, diff_documents
from core.patching.ltx.document import (
    LtxDocument,
    LtxEntry,
    LtxSectionModel,
    parse_ltx_document,
)
from core.patching.ltx.generator import DltxGenerator, PatchGenerationError
from core.patching.ltx.multiline import analyze_multiline_edit
from core.patching.models import (
    PatchIssue,
    PatchOptions,
    PatchPlan,
    PatchPlanningError,
    PatchTarget,
)
from core.patching.targets import (
    choose_load_last_suffix,
    existing_patch_names,
    patch_file_rel,
    preferred_suffix,
    resolve_patch_target,
    suggest_patch_mod_name,
)
from core.tools.fs import read_text_safe


def _issue_from_diff(issue) -> PatchIssue:
    return PatchIssue(
        code=issue.code,
        message=issue.message,
        severity=issue.severity,
        section=issue.section,
        key=issue.key,
    )


def _target_issue(target: PatchTarget) -> Optional[PatchIssue]:
    if target.resolved:
        return None

    message = target.reason or "Не удалось определить корень загрузки"

    if target.ambiguous and target.candidates:
        message += ": " + ", ".join(str(p) for p in target.candidates)

    return PatchIssue(
        code="ambiguous_target",
        message=message,
        severity="error",
    )


def _change_warnings(diff: DocumentDiff) -> List[PatchIssue]:
    warnings: List[PatchIssue] = []

    for change in diff.changes:
        if change.risk == "confirm":
            warnings.append(
                PatchIssue(
                    code="needs_confirmation",
                    message=(
                        "Изменение '%s' в секции [%s] требует подтверждения: %s"
                        % (
                            change.kind,
                            change.section,
                            change.note or "влияет на итоговую конфигурацию",
                        )
                    ),
                    severity="warning",
                    section=change.section,
                    key=change.key,
                )
            )

    return warnings


def _apply_property_to_document(
    document: LtxDocument,
    section_name: str,
    key_name: str,
    desired_value: Optional[str],
    delete: bool = False,
) -> LtxDocument:
    result = copy.deepcopy(document)
    name = (section_name or "").lower()
    section = result.sections.get(name)

    if section is None:
        section = LtxSectionModel(
            name=name,
            header_kind="safe",
            parents=[],
            entries=[],
            line_number=0,
        )
        result.sections[name] = section

    section.entries.append(
        LtxEntry(
            key=key_name,
            value=None if delete else desired_value,
            operation="delete" if delete else "set",
            line_number=0,
            raw="",
        )
    )

    return result


def _build_plan(
    source_path: Path,
    diff: DocumentDiff,
    target: PatchTarget,
    options: PatchOptions,
    forest=None,
) -> PatchPlan:
    issues: List[PatchIssue] = []
    warnings: List[PatchIssue] = []

    issues.extend(_issue_from_diff(issue) for issue in diff.issues)
    warnings.extend(_change_warnings(diff))

    for change in diff.changes:
        if change.risk == "unsupported":
            issues.append(
                PatchIssue(
                    code="unsupported_change",
                    message=(
                        "Изменение '%s' в секции [%s] нельзя безопасно "
                        "выразить патчем"
                        % (change.kind, change.section)
                    ),
                    severity="error",
                    section=change.section,
                    key=change.key,
                )
            )

        if change.kind == "delete_section" and not options.allow_delete_section:
            issues.append(
                PatchIssue(
                    code="delete_section_disabled",
                    message=(
                        "Удаление секции [%s] запрещено настройками плана"
                        % change.section
                    ),
                    severity="error",
                    section=change.section,
                )
            )

        if change.kind == "delete_key" and not options.allow_delete_key:
            issues.append(
                PatchIssue(
                    code="delete_key_disabled",
                    message=(
                        "Удаление ключа '%s' в [%s] запрещено настройками"
                        % (change.key, change.section)
                    ),
                    severity="error",
                    section=change.section,
                    key=change.key,
                )
            )

    target_issue = _target_issue(target)
    if target_issue is not None:
        issues.append(target_issue)

    patch_mod = options.patch_mod or (
        suggest_patch_mod_name(target.root_path)
        if target.root_path is not None
        else "ACE Patch"
    )

    patch_rel = None
    if target.root_path is not None:
        names = existing_patch_names(target.root_path)
        suffix = choose_load_last_suffix(
            target.root_path,
            names,
            preferred_suffix(options),
        )
        patch_rel = patch_file_rel(
            forest,
            target.root_path,
            suffix,
        )

    plan = PatchPlan(
        source_path=source_path,
        target=target,
        patch_mod=patch_mod,
        patch_file_rel=patch_rel,
        changes=tuple(diff.changes),
        issues=tuple(issues),
        warnings=tuple(warnings),
        preview="",
        diff=diff,
        snapshot_id=options.snapshot_id,
        encoding=options.encoding,
    )

    if plan.can_generate:
        try:
            preview = DltxGenerator().preview(plan)
        except PatchGenerationError as exc:
            issues.append(
                PatchIssue(
                    code="generation_failed",
                    message=str(exc),
                    severity="error",
                )
            )
            plan = replace(plan, issues=tuple(issues))
        else:
            plan = replace(plan, preview=preview)

    return plan




class LtxPlanner:
    """Планировщик LTX-патчей (корень, одна строка, многострочная правка)."""

    def __init__(self, resolver_adapter=None, cache=None):
        self.resolver_adapter = resolver_adapter
        self.cache = cache

    def plan_document_change(
        self,
        source_path,
        baseline_text: str,
        draft_text: str,
        forest=None,
        options: Optional[PatchOptions] = None,
    ) -> PatchPlan:
        """Построить план по тексту оригинала и черновика.

        ``baseline_text`` и ``draft_text`` должны быть текстом одного и
        того же физического файла. Передавать сюда resolved-текст нельзя:
        для правки итогового значения нужен слой provenance, иначе diff
        примет наследование и include за изменения файла.
        """
        source_path = Path(source_path)
        options = options or PatchOptions()

        if source_path.suffix.lower() != ".ltx":
            raise PatchPlanningError(
                "DLTX-план поддерживает только .ltx, получено: %s"
                % source_path
            )

        before = parse_ltx_document(baseline_text, source_path=source_path)
        after = parse_ltx_document(draft_text, source_path=source_path)
        diff = diff_documents(before, after)

        if diff.is_empty:
            target = resolve_patch_target(
                forest,
                source_path,
                options.target_root,
            )
            return PatchPlan(
                source_path=source_path,
                target=target,
                patch_mod=options.patch_mod or "ACE Patch",
                patch_file_rel=None,
                changes=(),
                issues=(
                    PatchIssue(
                        code="no_changes",
                        message="Черновик не отличается от оригинала",
                        severity="warning",
                    ),
                ),
                warnings=(),
                preview="",
                diff=diff,
                snapshot_id=options.snapshot_id,
                encoding=options.encoding,
            )

        target = resolve_patch_target(
            forest,
            source_path,
            options.target_root,
        )

        return _build_plan(source_path, diff, target, options, forest=forest)

    def plan_single_line_edit(
        self,
        source_path,
        baseline_text,
        edited_text,
        line_sources=(),
        forest=None,
        options: Optional[PatchOptions] = None,
    ) -> PatchPlan:
        """Построить патч по ровно одной изменённой строке.

        Поддерживаются все основные DLTX-инструкции: замена, добавление и
        удаление ключа, ``>``/``<``, родители, ``@[section]`` и
        ``!![section]``. Структурные многострочные правки блокируются.
        """
        source_path = Path(source_path)
        options = options or PatchOptions()

        if source_path.suffix.lower() != ".ltx":
            raise PatchPlanningError(
                "DLTX-план поддерживает только .ltx, получено: %s"
                % source_path
            )

        before = parse_ltx_document(baseline_text, source_path=source_path)
        after = parse_ltx_document(edited_text, source_path=source_path)
        analysis = analyze_single_line_edit(
            baseline_text,
            edited_text,
            line_sources=line_sources,
        )

        diff = DocumentDiff(
            before=before,
            after=after,
            changes=analysis.changes,
            issues=analysis.issues,
        )

        target = resolve_patch_target(
            forest,
            source_path,
            options.target_root,
        )

        return _build_plan(
            source_path,
            diff,
            target,
            options,
            forest=forest,
        )

    def plan_multiline_edit(
        self,
        source_path,
        baseline_text,
        edited_text,
        line_sources=(),
        forest=None,
        options: Optional[PatchOptions] = None,
        analysis=None,
    ) -> PatchPlan:
        """Построить один патч по всем изменениям одного файла.

        Поддерживаются несколько строк, вставка/удаление ключей и блоков,
        переименование секций и смена наследования. Противоречия
        разрешаются по итоговому тексту.

        ``analysis`` позволяет переиспользовать уже выполненный разбор
        ``analyze_multiline_edit``: на больших resolved-текстах повторный
        парсинг и diff стоят секунды и десятки секунд.
        """
        source_path = Path(source_path)
        options = options or PatchOptions()

        if source_path.suffix.lower() != ".ltx":
            raise PatchPlanningError(
                "DLTX-план поддерживает только .ltx, получено: %s"
                % source_path
            )

        if analysis is None:
            analysis = analyze_multiline_edit(
                baseline_text,
                edited_text,
                line_sources=line_sources,
            )

        diff = DocumentDiff(
            before=analysis.before,
            after=analysis.after,
            changes=analysis.changes,
            issues=analysis.issues,
        )

        target = resolve_patch_target(
            forest,
            source_path,
            options.target_root,
        )

        return _build_plan(
            source_path,
            diff,
            target,
            options,
            forest=forest,
        )

    def plan_text_patch(
        self,
        original_path,
        desired_text,
        target_mod=None,
    ):
        """Сравнить файл с желаемым текстом."""
        original_path = Path(original_path)

        if not original_path.is_file():
            raise PatchPlanningError("Файл не найден: %s" % original_path)

        baseline = read_text_safe(original_path)

        class _SelfForest:
            scan_dir = str(original_path.parent)
            files = [original_path]
            include_parents = {}

        options = PatchOptions(
            patch_mod=target_mod or "ACE Patch",
        )

        return self.plan_document_change(
            original_path,
            baseline,
            desired_text,
            _SelfForest(),
            options,
        )

    def plan_property_patch(
        self,
        original_path,
        section_name,
        key_name,
        desired_value,
        target_mod=None,
        forest=None,
        delete=False,
        options: Optional[PatchOptions] = None,
    ):
        """Построить план изменения одного ключа.

        Без семантической модели происхождения этот метод работает в
        пределах одного файла. Для изменения значения, пришедшего из
        другого include-файла, нужен слой provenance.
        """
        original_path = Path(original_path)

        if not original_path.is_file():
            raise PatchPlanningError("Файл не найден: %s" % original_path)

        baseline_text = read_text_safe(original_path)
        before = parse_ltx_document(baseline_text, source_path=original_path)
        after = _apply_property_to_document(
            before,
            section_name,
            key_name,
            desired_value,
            delete=delete,
        )
        diff = diff_documents(before, after)

        class _SelfForest:
            scan_dir = str(original_path.parent)
            files = [original_path]
            include_parents = {}

        if forest is None:
            forest = _SelfForest()

        options = options or PatchOptions(
            patch_mod=target_mod or "ACE Patch",
            allow_delete_key=True,
        )
        target = resolve_patch_target(forest, original_path)

        return _build_plan(original_path, diff, target, options, forest=forest)

