# -*- coding: utf-8 -*-
"""LTX-движок: адаптер над ``LtxPatchService`` и проверками фасада."""

from __future__ import annotations

from core.patching.engines.base import (
    PatchEngine,
    combine_verification,
    options_for_intent,
)
from core.patching.intent import PatchIntent, PatchSnapshot
from core.patching.models import (
    GeneratedPatch,
    PatchPlan,
    PatchResult,
    VerificationResult,
)


class LtxPatchEngine(PatchEngine):
    """Планирование, генерация и проверка LTX-патча."""

    kind = "ltx"

    def __init__(self, services):
        if services is None:
            raise ValueError("LtxPatchEngine требует services")
        self.services = services

    def plan(
        self,
        intent: PatchIntent,
        snapshot: PatchSnapshot,
    ) -> PatchResult:
        options = options_for_intent(intent, snapshot)
        return self.services.plan_ltx_patch(
            intent.source_path,
            intent.before_text,
            intent.after_text,
            mode=intent.mode or "root",
            line_sources=intent.line_sources,
            forest=snapshot.forest,
            options=options,
            resolved=intent.resolved,
            analysis=intent.analysis,
            verify_resolver=intent.verify_resolver,
        )

    def generate(self, plan: PatchPlan) -> GeneratedPatch:
        return self.services.generate_patch(plan)

    def verify(self, generated: GeneratedPatch) -> VerificationResult:
        # Объединённому плану без одного diff модельная проверка
        # неприменима: его проверяет Resolver4. Это то же правило, что
        # в ``CoreComposition.generate_patch``.
        if generated.plan.diff is None:
            return self.services.verify_patch_with_resolver(generated)
        model = self.services.verify_patch(generated)
        resolver = self.services.verify_patch_with_resolver(generated)
        return combine_verification(model, resolver)

    def preview(self, plan: PatchPlan) -> str:
        return self.services.patch.preview(plan)


__all__ = ["LtxPatchEngine"]
