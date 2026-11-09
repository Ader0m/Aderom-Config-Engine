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
from typing import List, Sequence, Tuple

from core.patching.models import (
    GeneratedPatch,
    PatchIssue,
    VerificationCheck,
    VerificationResult,
)
from core.patching.verifier import BasePatchVerifier
from core.patching.xml.diff import (
    XmlChange,
    XmlDocumentDiff,
    apply_xml_changes,
)
from core.patching.xml.document import (
    XmlDocument,
    XmlModelError,
    parse_xml_document,
)
from core.xml.changes import apply_changes_to_xml_obj


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


class XmlModelPatchVerifier(BasePatchVerifier):
    """Модельная проверка XML/DXML-патча."""

    target_error = "Не определён корень XML-патча"
    allowed_configs = (".xml", ".ltx")
    allow_scripts = True

    def verify(self, generated: GeneratedPatch) -> VerificationResult:
        checks: List[VerificationCheck] = []
        issues: List[PatchIssue] = []

        plan = generated.plan
        diff = plan.diff if isinstance(plan.diff, XmlDocumentDiff) else None

        target_check, target_issues = self.check_target(generated)
        checks.append(target_check)
        issues.extend(target_issues)

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

        path_check, path_issues = self.check_paths(generated)
        checks.append(path_check)
        issues.extend(path_issues)

        text_check, text_issues = self.check_text(generated)
        checks.append(text_check)
        issues.extend(text_issues)

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
