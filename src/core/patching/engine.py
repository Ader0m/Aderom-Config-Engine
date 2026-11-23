# -*- coding: utf-8 -*-
"""Оркестратор форматных патч-движков.

Движки только планируют, генерируют и проверяют. Единственная точка
записи — :class:`core.patching.commit.PatchCommitService`.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Dict, Optional

from core.patching.engines.asset import AssetPatchEngine
from core.patching.engines.base import PatchEngine
from core.patching.engines.ltx import LtxPatchEngine
from core.patching.engines.new_file import NewFilePatchEngine
from core.patching.engines.xml import XmlPatchEngine
from core.patching.intent import PatchBundle, PatchIntent, PatchSnapshot
from core.patching.models import (
    GeneratedPatch,
    PatchPlan,
    PatchResult,
    VerificationResult,
)


class PatchOrchestrator:
    """Выбирает движок по виду намерения и собирает ``PatchBundle``."""

    def __init__(self, services=None, engines=None):
        self.services = services
        if engines is None:
            engines = (
                LtxPatchEngine(services),
                XmlPatchEngine(services),
                AssetPatchEngine(services),
                NewFilePatchEngine(services),
            )
        self._engines: Dict[str, PatchEngine] = {
            engine.kind: engine for engine in engines
        }

    def engine(self, kind: str) -> PatchEngine:
        try:
            return self._engines[str(kind)]
        except KeyError:
            raise ValueError("Неизвестный вид патча: %s" % kind) from None

    def plan(
        self,
        intent: PatchIntent,
        snapshot: Optional[PatchSnapshot] = None,
    ) -> PatchResult:
        snapshot = snapshot or PatchSnapshot()
        return self.engine(intent.kind).plan(intent, snapshot)

    def build(
        self,
        intent: PatchIntent,
        snapshot: Optional[PatchSnapshot] = None,
    ) -> PatchBundle:
        snapshot = snapshot or PatchSnapshot()
        engine = self.engine(intent.kind)
        result = engine.plan(intent, snapshot)
        generated = result.generated
        verification = (
            generated.verification if generated is not None else None
        )

        if generated is None and result.plan.can_generate:
            generated = engine.generate(result.plan)
            verification = generated.verification

        if generated is not None and verification is None:
            verification = engine.verify(generated)
            generated = replace(generated, verification=verification)

        return PatchBundle(
            intent=intent,
            snapshot=snapshot,
            plan=result.plan,
            generated=generated,
            verification=verification,
            notes=tuple(result.notes or ()),
        )

__all__ = [
    "PatchEngine",
    "PatchOrchestrator",
]
