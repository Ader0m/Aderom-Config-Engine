# -*- coding: utf-8 -*-
"""Движок нового файла проекта (.ltx/.xml)."""

from __future__ import annotations

from pathlib import Path

from core.patching.engines.base import (
    PatchEngine,
    patch_mod_for_intent,
    require_relative_path,
    snapshot_id_for_intent,
)
from core.patching.intent import PatchIntent, PatchSnapshot
from core.patching.models import (
    GeneratedPatch,
    PatchFile,
    PatchIssue,
    PatchPlan,
    PatchPlanningError,
    PatchResult,
    PatchTarget,
    VerificationCheck,
    VerificationResult,
)


class NewFilePatchEngine(PatchEngine):
    """План и патч для нового файла внутри ``gamedata/configs``."""

    kind = "new_file"

    def __init__(self, services=None):
        self.services = services

    @staticmethod
    def _check_source(source_path: Path, text: str) -> None:
        suffix = source_path.suffix.lower()
        if suffix not in (".ltx", ".xml"):
            raise PatchPlanningError(
                "Новый файл должен быть .ltx или .xml"
            )
        if not text.strip():
            raise PatchPlanningError(
                "Содержимое нового файла пустое"
            )

    def plan(
        self,
        intent: PatchIntent,
        snapshot: PatchSnapshot,
    ) -> PatchResult:
        snapshot_id = snapshot_id_for_intent(intent, snapshot)
        source_path = Path(intent.source_path)
        text = str(intent.after_text or "")
        self._check_source(source_path, text)
        relative = require_relative_path(
            intent.relative_path,
            "нового файла в патч-моде",
        )
        suffix = source_path.suffix.lower()

        plan = PatchPlan(
            source_path=source_path,
            target=PatchTarget(
                source_path=source_path,
                root_path=None,
                reason="Новый файл проекта",
            ),
            patch_mod=patch_mod_for_intent(intent),
            patch_file_rel=relative,
            changes=(),
            issues=(),
            warnings=(),
            preview=text,
            snapshot_id=snapshot_id,
            format="xml" if suffix == ".xml" else "ltx",
        )
        generated = GeneratedPatch(
            plan=plan,
            files=(
                PatchFile(
                    relative_path=(
                        Path("gamedata") / "configs" / relative
                    ),
                    text=text,
                ),
            ),
            text=text,
            verification=VerificationResult(
                ok=True,
                checks=(
                    VerificationCheck(
                        name="new_file",
                        ok=True,
                        detail="Файл будет записан как новый",
                    ),
                ),
            ),
        )
        return PatchResult(plan=plan, generated=generated)

    def generate(self, plan: PatchPlan) -> GeneratedPatch:
        text = str(plan.preview or "")
        relative = require_relative_path(
            plan.patch_file_rel,
            "нового файла в патч-моде",
        )
        self._check_source(Path(plan.source_path), text)
        return GeneratedPatch(
            plan=plan,
            files=(
                PatchFile(
                    relative_path=(
                        Path("gamedata") / "configs" / relative
                    ),
                    text=text,
                ),
            ),
            text=text,
            verification=VerificationResult(
                ok=True,
                checks=(
                    VerificationCheck(
                        name="new_file",
                        ok=True,
                        detail="Файл будет записан как новый",
                    ),
                ),
            ),
        )

    def verify(self, generated: GeneratedPatch) -> VerificationResult:
        if generated.verification is not None:
            return generated.verification
        return VerificationResult(
            ok=False,
            checks=(
                VerificationCheck(
                    name="new_file",
                    ok=False,
                    detail="Проверка нового файла отсутствует",
                ),
            ),
            issues=(
                PatchIssue(
                    code="no_verification",
                    message="Проверка нового файла отсутствует",
                    severity="error",
                ),
            ),
        )

    def preview(self, plan: PatchPlan) -> str:
        return str(plan.preview or "")


__all__ = ["NewFilePatchEngine"]
