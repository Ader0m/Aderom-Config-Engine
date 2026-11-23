# -*- coding: utf-8 -*-
"""Движок двоичных ресурсов: файл записывается целиком."""

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


class AssetPatchEngine(PatchEngine):
    """План и патч для зарегистрированного двоичного ресурса."""

    kind = "asset"

    def __init__(self, services=None):
        self.services = services

    def plan(
        self,
        intent: PatchIntent,
        snapshot: PatchSnapshot,
    ) -> PatchResult:
        snapshot_id = snapshot_id_for_intent(intent, snapshot)
        relative = require_relative_path(
            intent.relative_path,
            "ресурса в патч-моде",
        )
        data = bytes(intent.asset_data or b"")
        source_path = Path(intent.source_path)
        patch_root = (
            Path(intent.patch_root)
            if intent.patch_root is not None
            else None
        )

        plan = PatchPlan(
            source_path=source_path,
            target=PatchTarget(
                source_path=source_path,
                root_path=patch_root,
                reason="Двоичный ресурс проекта",
            ),
            patch_mod=patch_mod_for_intent(intent),
            patch_file_rel=relative,
            changes=tuple(intent.changes),
            snapshot_id=snapshot_id,
            format="asset",
            patch_target_dir=intent.vfs_root,
            asset_mode=intent.asset_save_mode,
        )

        patch_rel = Path("gamedata") / relative
        generated = GeneratedPatch(
            plan=plan,
            files=(
                PatchFile(
                    relative_path=patch_rel,
                    text="",
                    data=data,
                ),
            ),
            text="",
            verification=VerificationResult(
                ok=True,
                checks=(
                    VerificationCheck(
                        name="asset",
                        ok=True,
                        detail="Файл будет записан целиком: %s"
                        % patch_rel,
                    ),
                ),
            ),
        )
        return PatchResult(plan=plan, generated=generated)

    def generate(self, plan: PatchPlan) -> GeneratedPatch:
        raise PatchPlanningError(
            "Двоичный ресурс генерируется вместе с планом: "
            "байты не хранятся в PatchPlan"
        )

    def verify(self, generated: GeneratedPatch) -> VerificationResult:
        if generated.verification is not None:
            return generated.verification
        return VerificationResult(
            ok=False,
            checks=(
                VerificationCheck(
                    name="asset",
                    ok=False,
                    detail="Проверка ресурса отсутствует",
                ),
            ),
            issues=(
                PatchIssue(
                    code="no_verification",
                    message="Проверка ресурса отсутствует",
                    severity="error",
                ),
            ),
        )

    def preview(self, plan: PatchPlan) -> str:
        return plan.preview


__all__ = ["AssetPatchEngine"]
