# -*- coding: utf-8 -*-
"""Общий каркас проверки сгенерированного патча.

Форматные проверки (LTX/XML) отличаются только round-trip-логикой.
Проверки цели, непустого текста и безопасных путей одинаковы, поэтому
живут здесь.
"""

from __future__ import annotations

from typing import List, Tuple

from core.patching.models import (
    GeneratedPatch,
    PatchIssue,
    VerificationCheck,
    VerificationResult,
)
from core.patching.targets import validate_patch_files


class BasePatchVerifier:
    """Общие проверки плана и путей для LTX/XML-патчей."""

    target_error = "Не определён корень загрузки для патча"
    target_error_code = "target_not_resolved"

    allowed_configs = (".ltx", ".xml")
    allow_scripts = False

    def check_target(self, generated: GeneratedPatch) -> Tuple[
        VerificationCheck,
        List[PatchIssue],
    ]:
        plan = generated.plan
        ok = plan.target.resolved
        check = VerificationCheck(
            name="target",
            ok=ok,
            detail=plan.target.reason,
        )
        issues: List[PatchIssue] = []
        if not ok:
            issues.append(
                PatchIssue(
                    code=self.target_error_code,
                    message=self.target_error,
                    severity="error",
                )
            )
        return check, issues

    def check_text(self, generated: GeneratedPatch) -> Tuple[
        VerificationCheck,
        List[PatchIssue],
    ]:
        ok = bool(generated.text.strip())
        check = VerificationCheck(
            name="text",
            ok=ok,
            detail="Текст патча не пуст" if ok else "Патч пуст",
        )
        issues: List[PatchIssue] = []
        if not ok:
            issues.append(
                PatchIssue(
                    code="empty_patch",
                    message="Сгенерированный патч пуст",
                    severity="error",
                )
            )
        return check, issues

    def check_paths(self, generated: GeneratedPatch) -> Tuple[
        VerificationCheck,
        List[PatchIssue],
    ]:
        ok, message = validate_patch_files(
            generated,
            allowed_configs=self.allowed_configs,
            allow_scripts=self.allow_scripts,
        )
        check = VerificationCheck(
            name="path",
            ok=ok,
            detail=message,
        )
        issues: List[PatchIssue] = []
        if not ok:
            issues.append(
                PatchIssue(
                    code="unsafe_path",
                    message=message,
                    severity="error",
                )
            )
        return check, issues

    @staticmethod
    def finish(
        checks: List[VerificationCheck],
        issues: List[PatchIssue],
    ) -> VerificationResult:
        ok = all(check.ok for check in checks) and not any(
            issue.severity == "error" for issue in issues
        )
        return VerificationResult(
            ok=ok,
            checks=tuple(checks),
            issues=tuple(issues),
        )

    def common_checks(
        self,
        generated: GeneratedPatch,
    ) -> Tuple[List[VerificationCheck], List[PatchIssue]]:
        checks: List[VerificationCheck] = []
        issues: List[PatchIssue] = []
        for func in (self.check_target, self.check_text, self.check_paths):
            check, found = func(generated)
            checks.append(check)
            issues.extend(found)
        return checks, issues
