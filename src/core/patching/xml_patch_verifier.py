# -*- coding: utf-8 -*-
"""Проверка XML/DXML-патча.

Две ступени:

1. модель: изменения применяются к ``diff.before`` и сравниваются с
   ``diff.after``;
2. виртуальный DXML: ``XmlResolver`` собирает include, применяет штатные
   DXML-патчи и виртуальный сгенерированный патч, после чего результат
   сравнивается с ожидаемым resolved-документом.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from ..documents.xml_diff import (
    XmlChange,
    XmlDocumentDiff,
    apply_xml_changes,
)
from ..documents.xml_model import (
    XmlDocument,
    XmlModelError,
    parse_xml_document,
    selector_segments,
)
from ..xml_dxml import XmlObject
from .patch_models import (
    GeneratedPatch,
    PatchIssue,
    VerificationCheck,
    VerificationResult,
)


def _path_is_safe(generated: GeneratedPatch) -> Tuple[bool, str]:
    for file in generated.files:
        rel = Path(file.relative_path)
        if rel.is_absolute() or ".." in rel.parts:
            return False, "Небезопасный путь патч-файла: %s" % rel

        parts = [part.lower() for part in rel.parts]
        if "overwrite" in parts:
            return False, "Патч не должен попадать в Overwrite"

        if parts[:2] == ["gamedata", "configs"]:
            if rel.suffix.lower() not in (".xml", ".ltx"):
                return False, "XML-патч должен иметь расширение .xml: %s" % rel
            continue

        if parts[:2] == ["gamedata", "scripts"]:
            if rel.suffix.lower() != ".script":
                return False, "DXML-патч должен иметь расширение .script: %s" % rel
            if len(parts) != 3 or not parts[2].startswith("modxml_"):
                return False, (
                    "DXML-скрипт должен лежать в gamedata/scripts и "
                    "начинаться с modxml_: %s" % rel
                )
            continue

        return False, "Патч должен лежать в gamedata/configs или gamedata/scripts: %s" % rel

    return True, ""


def _check_documents(
    actual: XmlDocument,
    expected: XmlDocument,
    name: str,
) -> Tuple[VerificationCheck, List[PatchIssue]]:
    ok = actual.canonical() == expected.canonical()
    check = VerificationCheck(
        name=name,
        ok=ok,
        detail=(
            "XML совпадает"
            if ok
            else "Структура XML после применения патча не совпала"
        ),
    )
    issues: List[PatchIssue] = []
    if not ok:
        issues.append(
            PatchIssue(
                code="xml_mismatch",
                message="XML после применения патча не совпал с ожидаемым",
                severity="error",
            )
        )
    return check, issues


class XmlModelPatchVerifier:
    """Модельная проверка XML/DXML-патча."""

    def verify(self, generated: GeneratedPatch) -> VerificationResult:
        checks: List[VerificationCheck] = []
        issues: List[PatchIssue] = []

        plan = generated.plan
        diff = plan.diff if isinstance(plan.diff, XmlDocumentDiff) else None

        target_ok = plan.target.resolved
        checks.append(
            VerificationCheck(
                name="target",
                ok=target_ok,
                detail=plan.target.reason,
            )
        )
        if not target_ok:
            issues.append(
                PatchIssue(
                    code="target_not_resolved",
                    message="Не определён корень XML-патча",
                    severity="error",
                )
            )

        files_ok = bool(generated.files)
        checks.append(
            VerificationCheck(
                name="files",
                ok=files_ok,
                detail="Файлы патча созданы" if files_ok else "Нет файлов патча",
            )
        )
        if not files_ok:
            issues.append(
                PatchIssue(
                    code="empty_patch",
                    message="Сгенерированный XML-патч пуст",
                    severity="error",
                )
            )
            return VerificationResult(False, tuple(checks), tuple(issues))

        path_ok, path_message = _path_is_safe(generated)
        checks.append(
            VerificationCheck(name="path", ok=path_ok, detail=path_message)
        )
        if not path_ok:
            issues.append(
                PatchIssue(
                    code="unsafe_path",
                    message=path_message,
                    severity="error",
                )
            )

        text_ok = bool(generated.text.strip())
        checks.append(
            VerificationCheck(
                name="text",
                ok=text_ok,
                detail="Текст патча не пуст" if text_ok else "Патч пуст",
            )
        )
        if not text_ok:
            issues.append(
                PatchIssue(
                    code="empty_patch",
                    message="Сгенерированный XML-патч пуст",
                    severity="error",
                )
            )

        if diff is None:
            checks.append(
                VerificationCheck(
                    name="diff",
                    ok=False,
                    detail="В плане нет XML-diff",
                )
            )
            issues.append(
                PatchIssue(
                    code="no_diff",
                    message="План не содержит XML-сравнения",
                    severity="error",
                )
            )
            return VerificationResult(
                ok=all(check.ok for check in checks),
                checks=tuple(checks),
                issues=tuple(issues),
            )

        mode = plan.xml_mode
        if mode == "dxml_lua":
            applied = apply_xml_changes(diff.before, plan.changes)
            check, compare_issues = _check_documents(
                applied,
                diff.after,
                "xml_roundtrip",
            )
            checks.append(check)
            issues.extend(compare_issues)

            lua_ok = "RegisterScriptCallback" in generated.text
            checks.append(
                VerificationCheck(
                    name="dxml_callback",
                    ok=lua_ok,
                    detail=(
                        "DXML-колбэк зарегистрирован"
                        if lua_ok
                        else "В скрипте нет RegisterScriptCallback"
                    ),
                )
            )
            if not lua_ok:
                issues.append(
                    PatchIssue(
                        code="dxml_callback_missing",
                        message="Сгенерированный DXML-скрипт не регистрирует колбэк",
                        severity="error",
                    )
                )

        elif mode == "new_file":
            additions = [
                change for change in plan.changes
                if change.kind == "xml_add_element"
            ]
            fragment_ok = self._check_additions(
                generated.text,
                additions,
                checks,
                issues,
            )
            if not fragment_ok:
                pass

        elif mode == "mixed":
            non_add = [
                change for change in plan.changes
                if change.kind != "xml_add_element"
            ]
            expected = apply_xml_changes(diff.before, non_add)
            full_file = next(
                (
                    file for file in generated.files
                    if Path(file.relative_path).suffix.lower() == ".xml"
                    and Path(file.relative_path).name
                    != Path(plan.xml_new_file_rel or "").name
                ),
                generated.files[0],
            )
            try:
                actual = parse_xml_document(full_file.text)
            except XmlModelError as exc:
                checks.append(
                    VerificationCheck(
                        name="xml_parse",
                        ok=False,
                        detail=str(exc),
                    )
                )
                issues.append(
                    PatchIssue(
                        code="xml_parse_failed",
                        message=str(exc),
                        severity="error",
                    )
                )
            else:
                check, compare_issues = _check_documents(
                    actual,
                    expected,
                    "xml_full_copy",
                )
                checks.append(check)
                issues.extend(compare_issues)

            additions = [
                change for change in plan.changes
                if change.kind == "xml_add_element"
            ]
            new_file = next(
                (
                    file for file in generated.files
                    if plan.xml_new_file_rel is not None
                    and Path(file.relative_path).name
                    == Path(plan.xml_new_file_rel).name
                ),
                generated.files[-1],
            )
            self._check_additions(
                new_file.text,
                additions,
                checks,
                issues,
            )

        else:
            try:
                actual = parse_xml_document(generated.text)
            except XmlModelError as exc:
                checks.append(
                    VerificationCheck(
                        name="xml_parse",
                        ok=False,
                        detail=str(exc),
                    )
                )
                issues.append(
                    PatchIssue(
                        code="xml_parse_failed",
                        message=str(exc),
                        severity="error",
                    )
                )
            else:
                check, compare_issues = _check_documents(
                    actual,
                    diff.after,
                    "xml_full_copy",
                )
                checks.append(check)
                issues.extend(compare_issues)

        ok = all(check.ok for check in checks) and not any(
            issue.severity == "error" for issue in issues
        )
        return VerificationResult(
            ok=ok,
            checks=tuple(checks),
            issues=tuple(issues),
        )

    def _check_additions(
        self,
        text: str,
        additions: Sequence[XmlChange],
        checks: List[VerificationCheck],
        issues: List[PatchIssue],
    ) -> bool:
        try:
            document = parse_xml_document(text)
        except XmlModelError as exc:
            checks.append(
                VerificationCheck(name="xml_parse", ok=False, detail=str(exc))
            )
            issues.append(
                PatchIssue(
                    code="xml_parse_failed",
                    message=str(exc),
                    severity="error",
                )
            )
            return False

        actual = {
            node.canonical() for node in document.roots()
        }
        missing = []
        for change in additions:
            try:
                node_doc = parse_xml_document(change.node_xml)
            except XmlModelError:
                missing.append(change.element)
                continue
            roots = node_doc.roots()
            if not roots:
                missing.append(change.element)
                continue
            if roots[0].canonical() not in actual:
                missing.append(change.element)

        ok = not missing
        checks.append(
            VerificationCheck(
                name="xml_additions",
                ok=ok,
                detail=(
                    "Добавленные элементы присутствуют"
                    if ok
                    else "Не найдены добавленные элементы: %s"
                    % ", ".join(missing)
                ),
            )
        )
        if not ok:
            issues.append(
                PatchIssue(
                    code="xml_additions_missing",
                    message="Новый XML не содержит всех добавленных элементов",
                    severity="error",
                )
            )
        return ok


# ---------------------------------------------------------------- virtual DXML


def _find_xml_obj_element(
    xml_obj: XmlObject,
    document: XmlDocument,
    path,
):
    node = document.find(path)
    if node is None:
        return None
    selector, ordinal, is_root = selector_segments(document, node)
    if is_root:
        return xml_obj.get_root()
    result = xml_obj.query(selector)
    index = max(0, ordinal - 1)
    return result[index] if index < len(result) else None


def apply_changes_to_xml_obj(
    xml_obj: XmlObject,
    document: XmlDocument,
    changes: Sequence[XmlChange],
) -> None:
    """Применить изменения плана к живому ``xml_obj`` DXML.

    Атрибуты применяются последними: селекторы потомков используют
    старые ``id``/``name`` и должны найти родителя до их изменения.
    """
    ordered = [
        change for change in changes
        if change.kind not in ("xml_set_attr", "xml_add_attr", "xml_delete_attr")
    ] + [
        change for change in changes
        if change.kind in ("xml_set_attr", "xml_add_attr", "xml_delete_attr")
    ]

    for change in ordered:
        try:
            element = _find_xml_obj_element(xml_obj, document, change.path)
        except Exception:
            element = None

        if change.kind in ("xml_set_attr", "xml_add_attr"):
            if element is not None:
                xml_obj.set_element_attr(
                    element,
                    {change.attribute or "": change.new_value or ""},
                )
        elif change.kind == "xml_delete_attr":
            if element is not None:
                xml_obj.remove_element_attr(
                    element,
                    [change.attribute or ""],
                )
        elif change.kind == "xml_set_text":
            if element is not None:
                xml_obj.set_text(element, change.new_value or "")
        elif change.kind == "xml_delete_element":
            if element is not None:
                xml_obj.remove_element(element)
        elif change.kind == "xml_add_element":
            parent = (
                _find_xml_obj_element(xml_obj, document, change.parent_path)
                if change.parent_path
                else xml_obj.xml_table
            )
            if parent is not None:
                xml_obj.insert_from_xml_string(
                    change.node_xml,
                    parent,
                    max(1, int(change.position or 1)),
                )
        elif change.kind == "xml_replace_element":
            if element is not None:
                parent = element.get("parent") if isinstance(element, dict) else None
                position = xml_obj.get_element_position(element) or 1
                xml_obj.remove_element(element)
                if parent is not None:
                    xml_obj.insert_from_xml_string(
                        change.node_xml,
                        parent,
                        position,
                    )


class XmlResolverPatchVerifier:
    """Виртуальная проверка DXML-патча через XmlResolver."""

    def __init__(self, xml_service=None):
        self.xml_service = xml_service

    def verify(self, generated: GeneratedPatch) -> VerificationResult:
        checks: List[VerificationCheck] = []
        issues: List[PatchIssue] = []

        plan = generated.plan
        diff = plan.diff if isinstance(plan.diff, XmlDocumentDiff) else None
        if diff is None:
            return VerificationResult(
                ok=False,
                checks=(
                    VerificationCheck(
                        name="resolver",
                        ok=False,
                        detail="Нет XML-diff для виртуальной проверки",
                    ),
                ),
                issues=(
                    PatchIssue(
                        code="no_diff",
                        message="План не содержит XML-сравнения",
                        severity="error",
                    ),
                ),
            )

        if plan.xml_mode != "dxml_lua":
            return VerificationResult(
                ok=True,
                checks=(
                    VerificationCheck(
                        name="resolver",
                        ok=True,
                        detail="Для XML-файла виртуальный DXML не требуется",
                    ),
                ),
                issues=(),
            )

        if self.xml_service is None:
            checks.append(
                VerificationCheck(
                    name="resolver",
                    ok=False,
                    detail="XmlResolveService не передан",
                )
            )
            issues.append(
                PatchIssue(
                    code="resolver_missing",
                    message="Нет XML-резолвера для виртуальной проверки DXML",
                    severity="error",
                )
            )
            return VerificationResult(False, tuple(checks), tuple(issues))

        try:
            root_path = plan.target.root_path or plan.source_path
            roots = list(plan.xml_roots or ())
            if len(roots) > 1 and plan.config_dir is not None:
                expected = diff.before.canonical()
                for candidate in roots:
                    try:
                        baseline = self.xml_service.resolve_output(
                            candidate,
                            plan.config_dir,
                            log=None,
                        )
                        baseline_doc = parse_xml_document(baseline.text)
                    except Exception:
                        continue
                    if baseline_doc.canonical() == expected:
                        root_path = candidate
                        break

            result = self.xml_service.resolve_with_virtual_xml_patch(
                plan.source_path,
                root_path,
                plan,
                log=None,
            )
        except Exception as exc:
            checks.append(
                VerificationCheck(
                    name="resolver",
                    ok=False,
                    detail=str(exc),
                )
            )
            issues.append(
                PatchIssue(
                    code="resolver_failed",
                    message="XmlResolver не смог проверить DXML-патч: %s" % exc,
                    severity="error",
                )
            )
            return VerificationResult(False, tuple(checks), tuple(issues))

        checks.append(
            VerificationCheck(
                name="resolver",
                ok=True,
                detail="XmlResolver выполнил include + DXML + виртуальный патч",
            )
        )

        try:
            actual = parse_xml_document(result.text)
        except XmlModelError as exc:
            checks.append(
                VerificationCheck(name="xml_parse", ok=False, detail=str(exc))
            )
            issues.append(
                PatchIssue(
                    code="xml_parse_failed",
                    message=str(exc),
                    severity="error",
                )
            )
            return VerificationResult(False, tuple(checks), tuple(issues))

        check, compare_issues = _check_documents(
            actual,
            diff.after,
            "resolver:xml",
        )
        checks.append(check)
        issues.extend(compare_issues)

        ok = all(check.ok for check in checks) and not any(
            issue.severity == "error" for issue in issues
        )
        return VerificationResult(
            ok=ok,
            checks=tuple(checks),
            issues=tuple(issues),
        )
