# -*- coding: utf-8 -*-
"""Порт патчей: намерение, план, генерация, проверка и движок."""

from core.patching.commit import (
    CommitAuthorization,
    PatchCommitError,
    PatchCommitResult,
    PatchCommitService,
)
from core.patching.engine import PatchEngine, PatchOrchestrator
from core.patching.intent import PatchBundle, PatchIntent, PatchSnapshot
from core.patching.engines.asset import AssetPatchEngine
from core.patching.engines.new_file import NewFilePatchEngine
from core.patching.ltx.merge import merge_patch_plans
from core.patching.models import (
    GeneratedPatch,
    PatchFile,
    PatchIssue,
    PatchOptions,
    PatchPlan,
    PatchPlanningError,
    PatchResult,
    PatchTarget,
    VerificationCheck,
    VerificationResult,
)
from core.patching.roundtrip import RoundTripReport
from core.patching.xml.merge import merge_xml_generated

__all__ = [
    "AssetPatchEngine",
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
    "PatchPlanningError",
    "PatchResult",
    "PatchSnapshot",
    "PatchTarget",
    "NewFilePatchEngine",
    "RoundTripReport",
    "VerificationCheck",
    "VerificationResult",
    "merge_patch_plans",
    "merge_xml_generated",
]
