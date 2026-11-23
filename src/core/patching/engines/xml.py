# -*- coding: utf-8 -*-
"""XML/DXML-движок: адаптер над ``XmlPatchService``."""

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


class XmlPatchEngine(PatchEngine):
    """Планирование, генерация и проверка XML/DXML-патча."""

    kind = "xml"

    def __init__(self, services):
        if services is None:
            raise ValueError("XmlPatchEngine требует services")
        self.services = services

    def plan(
        self,
        intent: PatchIntent,
        snapshot: PatchSnapshot,
    ) -> PatchResult:
        options = options_for_intent(intent, snapshot)
        return self.services.plan_xml_patch(
            intent.source_path,
            intent.before_text,
            intent.after_text,
            mode=intent.mode or "auto",
            line_sources=intent.line_sources,
            forest=snapshot.forest,
            options=options,
            project_mode=intent.project_mode or "root",
            overlay=intent.overlay or snapshot.overlay,
        )

    def generate(self, plan: PatchPlan) -> GeneratedPatch:
        return self.services.generate_patch(plan)

    def verify(self, generated: GeneratedPatch) -> VerificationResult:
        model = self.services.verify_patch(generated)
        resolver = self.services.verify_patch_with_resolver(generated)
        return combine_verification(model, resolver)

    def preview(self, plan: PatchPlan) -> str:
        return self.services.patch.preview(plan)


__all__ = ["XmlPatchEngine"]
