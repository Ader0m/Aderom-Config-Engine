# -*- coding: utf-8 -*-
"""PatchPlanner — уровень «Сохранить как»: план изменения.

Планировщик не пишет файлы. Он сравнивает оригинал и черновик, определяет
корень загрузки, собирает риски и готовит предпросмотр DLTX- или
XML/DXML-патча.

Опасные и неоднозначные изменения не исчезают: они попадают в ``issues``
и блокируют генерацию, пока пользователь явно не разрешит их.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
from typing import List, Optional

from ..documents.document_diff import DocumentDiff, diff_documents
from ..documents.document_model import (
    LtxDocument,
    LtxEntry,
    LtxSectionModel,
    parse_ltx_document,
)
from ..documents.multiline_edit import analyze_multiline_edit
from ..documents.single_line_edit import analyze_single_line_edit
from ..documents.xml_diff import XmlDocumentDiff, XmlDiffIssue, diff_xml_documents
from ..documents.xml_model import XmlModelError, parse_xml_document
from .dltx_generator import DltxGenerator, PatchGenerationError
from .dxml_generator import DxmlGenerator
from ..fs import read_text_safe
from .patch_models import PatchIssue, PatchOptions, PatchPlan, PatchTarget
from .patch_targets import (
    choose_load_last_suffix,
    patch_file_rel,
    resolve_patch_target,
    suggest_patch_mod_name,
    xml_patch_file_rel,
    xml_relative_path,
    xml_root_candidates,
    xml_root_virtual_names,
)


class PatchPlanningError(Exception):
    """План нельзя построить."""


def _issue_from_diff(issue) -> PatchIssue:
    return PatchIssue(
        code=issue.code,
        message=issue.message,
        severity=issue.severity,
        section=issue.section,
        key=issue.key,
    )


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


# Кэш списка mod_*.ltx в каталоге корня. Планирование вызывается для
# каждого изменённого файла, а каталог gamedata/configs может содержать
# тысячи файлов; повторный listdir на каждый план не нужен.
_PATCH_NAMES_CACHE = {}


def _existing_patch_names(root_path: Path) -> List[str]:
    parent = root_path.parent
    key = str(parent).lower()

    try:
        mtime = parent.stat().st_mtime_ns
    except OSError:
        return []

    cached = _PATCH_NAMES_CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return list(cached[1])

    try:
        names = [
            entry.name
            for entry in parent.iterdir()
            if entry.is_file() and entry.name.lower().startswith("mod_")
        ]
    except OSError:
        return []

    _PATCH_NAMES_CACHE[key] = (mtime, tuple(names))
    return names


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
        names = _existing_patch_names(target.root_path)
        preferred = options.suffix or "ace"
        if options.project_tag:
            preferred = "%s_%s" % (preferred, options.project_tag)

        suffix = choose_load_last_suffix(
            target.root_path,
            names,
            preferred,
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


def _xml_script_suffix(options: PatchOptions) -> str:
    suffix = options.suffix or "ace"
    if options.project_tag:
        suffix = "%s_%s" % (suffix, options.project_tag)
    return "zzz_%s" % suffix


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
    from .patch_targets import sanitize_name

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


class PatchPlanner:
    """Планировщик патчей."""

    def __init__(self, resolver_adapter=None, cache=None):
        self.resolver_adapter = resolver_adapter
        self.cache = cache

    # -------------------------------------------------- XML
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

    def preview(self, plan: PatchPlan) -> str:
        if plan.is_xml:
            return plan.preview or DxmlGenerator().preview(plan)
        return plan.preview or DltxGenerator().preview(plan)
