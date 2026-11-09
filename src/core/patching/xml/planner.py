# -*- coding: utf-8 -*-
"""Планирование XML/DXML-патчей."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import List, Optional, Tuple

from core.patching.models import (
    PatchIssue,
    PatchOptions,
    PatchPlan,
    PatchPlanningError,
    PatchTarget,
)
from core.patching.targets import (
    preferred_suffix,
    sanitize_name,
    xml_patch_file_rel,
    xml_relative_path,
    xml_root_candidates,
    xml_root_virtual_names,
)
from core.patching.xml.diff import (
    XmlDiffIssue,
    XmlDocumentDiff,
    diff_xml_documents,
)
from core.patching.xml.document import XmlModelError, parse_xml_document
from core.patching.xml.generator import DxmlGenerator


def _xml_issue_from_diff(issue: XmlDiffIssue) -> PatchIssue:
    return PatchIssue(
        code=issue.code,
        message=issue.message,
        severity=issue.severity,
        section=(
            "/".join(
                tag if index == 0 else "%s[%d]" % (tag, index + 1)
                for tag, index in issue.path
            )
            or None
        ),
        key=issue.attribute,
    )


def _xml_display_path(path) -> str:
    return "/".join(
        tag if index == 0 else "%s[%d]" % (tag, index + 1)
        for tag, index in path
    )



def _xml_script_suffix(options: PatchOptions) -> str:
    return "zzz_%s" % preferred_suffix(options)


def _choose_xml_mode(
    diff: XmlDocumentDiff,
    options: PatchOptions,
    resolved: bool,
) -> str:
    requested = (options.xml_patch_mode or "auto").strip().lower()

    if requested == "xml":
        # Пользователь выбрал XML-файлы: для сырого файла это auto
        # (новая XML-копия или полная копия), для resolved — ошибка,
        # которую добавит _build_xml_plan.
        requested = "auto" if not resolved else "full_copy"

    if requested not in ("auto", "full_copy", "new_file", "mixed", "dxml_lua"):
        raise PatchPlanningError(
            "Неизвестный режим XML-патча: %s" % requested
        )

    if requested != "auto":
        return requested

    if resolved:
        return "dxml_lua"

    changes = diff.changes
    additions = [
        change for change in changes
        if change.kind == "xml_add_element"
    ]
    others = [
        change for change in changes
        if change.kind != "xml_add_element"
    ]

    if additions and others:
        return "mixed"
    if additions:
        return "new_file"
    return "full_copy"


def _new_xml_name(source_path: Path, options: PatchOptions) -> str:
    if options.xml_new_file_name:
        return options.xml_new_file_name
    tag = options.project_tag or "ace"
    base = Path(source_path).stem
    from core.patching.targets import sanitize_name

    return "%s_ace_%s.xml" % (
        sanitize_name(base, "xml"),
        sanitize_name(tag, "ace"),
    )


def _build_xml_plan(
    source_path: Path,
    diff: XmlDocumentDiff,
    options: PatchOptions,
    forest=None,
    resolved: bool = False,
    line_sources=(),
    after_text: str = "",
) -> PatchPlan:
    issues: List[PatchIssue] = []
    warnings: List[PatchIssue] = []

    issues.extend(
        _xml_issue_from_diff(issue) for issue in diff.issues
    )

    for change in diff.changes:
        if change.risk == "confirm":
            warnings.append(
                PatchIssue(
                    code="needs_confirmation",
                    message=(
                        "Изменение '%s' (%s) требует подтверждения: %s"
                        % (
                            change.kind,
                            _xml_display_path(change.path) or change.element,
                            change.note or "влияет на итоговый XML",
                        )
                    ),
                    severity="warning",
                    section=_xml_display_path(change.path),
                    key=change.key,
                )
            )
        if (
            change.kind == "xml_delete_element"
            and not options.allow_delete_xml_element
        ):
            issues.append(
                PatchIssue(
                    code="delete_xml_element_disabled",
                    message=(
                        "Удаление элемента %s запрещено настройками плана"
                        % _xml_display_path(change.path)
                    ),
                    severity="error",
                    section=_xml_display_path(change.path),
                )
            )

    mode = _choose_xml_mode(diff, options, resolved)

    if mode == "mixed" and (diff.before.includes or diff.after.includes):
        mode = "full_copy"
        warnings.append(
            PatchIssue(
                code="xml_mixed_includes",
                message=(
                    "В XML есть #include: добавления записаны в полную "
                    "копию файла, чтобы не потерять include, комментарии "
                    "и форматирование"
                ),
                severity="warning",
            )
        )

    config_dir = None
    if forest is not None:
        scan_dir = getattr(forest, "scan_dir", None)
        config_dir = Path(scan_dir) if scan_dir else None

    roots: Tuple[Path, ...] = ()
    root_names: Tuple[str, ...] = ()
    xml_new_rel: Optional[Path] = None

    anchor_path = source_path
    if mode in ("new_file", "mixed") and forest is not None:
        candidates = xml_root_candidates(forest, source_path)
        if candidates and source_path not in candidates:
            anchor_path = candidates[0]
            if len(candidates) > 1:
                warnings.append(
                    PatchIssue(
                        code="xml_new_file_multiple_roots",
                        message=(
                            "Файл входит в несколько XML-корней; новый XML "
                            "создаётся рядом с первым: %s"
                            % anchor_path
                        ),
                        severity="warning",
                    )
                )

    if mode == "dxml_lua":
        if forest is not None:
            roots = xml_root_candidates(forest, source_path)
        if not roots:
            roots = (source_path,)
        if config_dir is None:
            config_dir = source_path.parent
        root_names = xml_root_virtual_names(config_dir, roots)
        if not resolved:
            warnings.append(
                PatchIssue(
                    code="xml_raw_as_dxml",
                    message=(
                        "DXML-режим для сырого XML применяется к resolved-"
                        "документу корня. Узлы, пришедшие из #include, могут "
                        "не совпасть по пути — проверьте предпросмотр."
                    ),
                    severity="warning",
                )
            )

    if mode == "dxml_lua":
        patch_rel = xml_patch_file_rel(
            forest,
            _xml_script_suffix(options),
        )
        target_root = roots[0] if roots else source_path
        target = PatchTarget(
            source_path=source_path,
            root_path=target_root,
            candidates=roots or (source_path,),
            reason=(
                "DXML применяется к корню загрузки"
                if len(roots) <= 1
                else "DXML применяется ко всем корням, включающим файл"
            ),
            ambiguous=False,
        )
        format_name = "dxml"
        encoding = "utf-8-sig"
        patch_dir = "scripts"
        warnings.append(
            PatchIssue(
                code="xml_dxml_patch",
                message=(
                    "Изменения будут применяться DXML-Lua-скриптом при "
                    "чтении XML: %s"
                    % ", ".join(root_names)
                ),
                severity="warning",
            )
        )
    elif mode == "new_file":
        rel = xml_relative_path(forest, anchor_path)
        xml_new_rel = Path(rel.parent) / _new_xml_name(anchor_path, options)
        patch_rel = xml_new_rel
        target = PatchTarget(
            source_path=source_path,
            root_path=source_path,
            candidates=(source_path,),
            reason="Новый XML-файл с добавленными элементами",
            ambiguous=False,
        )
        format_name = "xml"
        encoding = "windows-1251"
        patch_dir = "configs"
        warnings.append(
            PatchIssue(
                code="xml_new_file",
                message=(
                    "Добавленные элементы будут записаны в новый XML-файл; "
                    "исходный файл не изменяется. Убедитесь, что игра "
                    "загружает новый файл (#include или ссылка по id/name)."
                ),
                severity="warning",
            )
        )
    elif mode == "mixed":
        rel = xml_relative_path(forest, source_path)
        patch_rel = rel
        anchor_rel = xml_relative_path(forest, anchor_path)
        xml_new_rel = (
            Path(anchor_rel.parent) / _new_xml_name(anchor_path, options)
        )
        target = PatchTarget(
            source_path=source_path,
            root_path=source_path,
            candidates=(source_path,),
            reason="Полная копия для правок + новый XML для добавлений",
            ambiguous=False,
        )
        format_name = "xml"
        encoding = "windows-1251"
        patch_dir = "configs"
        warnings.append(
            PatchIssue(
                code="xml_mixed_patch",
                message=(
                    "Правки существующих узлов будут записаны полной "
                    "копией XML-файла, а добавленные элементы — отдельным "
                    "новым XML-файлом. Убедитесь, что игра загружает новый "
                    "файл (#include или ссылка по id/name)."
                ),
                severity="warning",
            )
        )
    else:
        rel = xml_relative_path(forest, source_path)
        patch_rel = rel
        target = PatchTarget(
            source_path=source_path,
            root_path=source_path,
            candidates=(source_path,),
            reason="Полная копия изменённого XML-файла",
            ambiguous=False,
        )
        format_name = "xml"
        encoding = "windows-1251"
        patch_dir = "configs"
        warnings.append(
            PatchIssue(
                code="xml_full_copy",
                message=(
                    "XML-файл будет записан в патч-мод целиком. Это "
                    "перекрывает файл из других модов, но не меняет оригинал."
                ),
                severity="warning",
            )
        )

    if resolved and mode not in ("dxml_lua", "full_copy"):
        issues.append(
            PatchIssue(
                code="resolved_xml_requires_dxml",
                message=(
                    "Правка resolved-вида XML выражается DXML-Lua-скриптом "
                    "или режимом Override (полная копия): полная копия "
                    "содержала бы уже собранные include и DXML-изменения"
                ),
                severity="error",
            )
        )

    if resolved and diff.before.fragment and mode != "full_copy":
        issues.append(
            PatchIssue(
                code="xml_resolved_fragment",
                message=(
                    "Resolved-вид этого файла является фрагментом из "
                    "нескольких корневых элементов. Надёжно адресовать его "
                    "узлы в DXML нельзя: откройте корневой XML или правьте "
                    "исходный файл."
                ),
                severity="error",
            )
        )

    if mode == "dxml_lua" and diff.before.fragment:
        issues.append(
            PatchIssue(
                code="xml_dxml_fragment",
                message=(
                    "DXML-патч нельзя построить для XML-фрагмента без "
                    "корневого контекста: выберите XML-файл или полную копию"
                ),
                severity="error",
            )
        )

    if resolved and any(
        getattr(src, "kind", None) == "patch" for src in line_sources or ()
    ):
        warnings.append(
            PatchIssue(
                code="xml_resolved_patch_source",
                message=(
                    "Часть resolved-строк пришла из DXML-патча; изменение "
                    "будет применено поверх него и может конфликтовать"
                ),
                severity="warning",
            )
        )

    plan = PatchPlan(
        source_path=source_path,
        target=target,
        patch_mod=options.patch_mod or "ACE Patch",
        patch_file_rel=patch_rel,
        changes=tuple(diff.changes),
        issues=tuple(issues),
        warnings=tuple(warnings),
        preview="",
        diff=diff,
        snapshot_id=options.snapshot_id,
        encoding=encoding,
        format=format_name,
        xml_mode=mode,
        xml_roots=tuple(roots),
        xml_root_names=tuple(root_names),
        xml_new_file_rel=xml_new_rel,
        xml_new_root_tag=options.xml_new_root_tag,
        config_dir=config_dir,
        patch_target_dir=patch_dir,
        after_text=after_text,
    )

    if diff.is_empty:
        plan = replace(
            plan,
            issues=tuple(issues)
            + (
                PatchIssue(
                    code="no_changes",
                    message="XML-черновик не отличается от оригинала",
                    severity="warning",
                ),
            ),
        )
        return plan

    if plan.can_generate:
        try:
            preview = DxmlGenerator().preview(plan)
        except Exception as exc:
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




class XmlPlanner:
    """Планировщик XML/DXML-патчей."""

    def __init__(self, resolver_adapter=None, cache=None):
        self.resolver_adapter = resolver_adapter
        self.cache = cache

    def plan_xml_document_change(
        self,
        source_path,
        baseline_text: str,
        draft_text: str,
        forest=None,
        options: Optional[PatchOptions] = None,
    ) -> PatchPlan:
        """Построить план изменения сырого XML-файла."""
        source_path = Path(source_path)
        options = options or PatchOptions()

        if source_path.suffix.lower() != ".xml":
            raise PatchPlanningError(
                "XML-план поддерживает только .xml, получено: %s"
                % source_path
            )

        try:
            before = parse_xml_document(baseline_text, source_path=source_path)
            after = parse_xml_document(draft_text, source_path=source_path)
        except XmlModelError as exc:
            raise PatchPlanningError(str(exc))

        diff = diff_xml_documents(before, after)
        return _build_xml_plan(
            source_path,
            diff,
            options,
            forest=forest,
            resolved=False,
            after_text=draft_text,
        )

    def plan_xml_resolved_edit(
        self,
        source_path,
        baseline_text: str,
        edited_text: str,
        line_sources=(),
        forest=None,
        options: Optional[PatchOptions] = None,
    ) -> PatchPlan:
        """Построить план правки resolved-вида XML (DXML).

        Полная копия resolved-текста не используется: она содержала бы уже
        развёрнутые include и чужие DXML-изменения. Поэтому правка
        выражается DXML-Lua-скриптом.
        """
        source_path = Path(source_path)
        options = options or PatchOptions()

        if source_path.suffix.lower() != ".xml":
            raise PatchPlanningError(
                "XML-план поддерживает только .xml, получено: %s"
                % source_path
            )

        try:
            before = parse_xml_document(baseline_text, source_path=source_path)
            after = parse_xml_document(edited_text, source_path=source_path)
        except XmlModelError as exc:
            raise PatchPlanningError(str(exc))

        diff = diff_xml_documents(before, after)
        return _build_xml_plan(
            source_path,
            diff,
            options,
            forest=forest,
            resolved=True,
            line_sources=line_sources,
            after_text=edited_text,
        )

