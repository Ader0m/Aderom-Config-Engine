# -*- coding: utf-8 -*-
"""Патч-конвейер: план, генерация, проверка, слияние и запись.

Форматные входы:

- :class:`core.patching.ltx.LtxPatchService`;
- :class:`core.patching.xml.XmlPatchService`.

Общие модели и запись патча не зависят от формата.
"""

from core.patching.commit import (
    CommitAuthorization,
    PatchCommitError,
    PatchCommitResult,
    PatchCommitService,
)
from core.patching.engine import PatchEngine, PatchOrchestrator
from core.patching.intent import PatchBundle, PatchIntent, PatchSnapshot
from core.patching.roundtrip import RoundTripReport
from core.patching.models import (
    GeneratedPatch,
    PatchFile,
    PatchIssue,
    PatchOptions,
    PatchPlan,
    PatchResult,
    PatchTarget,
    VerificationCheck,
    VerificationResult,
)

__all__ = [
    "CommitAuthorization",
    "GeneratedPatch",
    "PatchBundle",
    "PatchCommitError",
    "PatchCommitResult",
    "PatchCommitService",
    "PatchEngine",
    "PatchFile",
    "PatchIntent",
    "PatchIssue",
    "PatchOptions",
    "PatchOrchestrator",
    "PatchPlan",
    "PatchResult",
    "PatchSnapshot",
    "PatchTarget",
    "RoundTripReport",
    "VerificationCheck",
    "VerificationResult",
    "LtxPatchService",
    "XmlPatchService",
]


def __getattr__(name):
    if name == "LtxPatchService":
        from core.patching.ltx.service import LtxPatchService

        return LtxPatchService
    if name == "XmlPatchService":
        from core.patching.xml.service import XmlPatchService

        return XmlPatchService
    raise AttributeError(name)
