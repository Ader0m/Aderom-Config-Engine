# -*- coding: utf-8 -*-
"""Порт патчей: план, генерация и проверка."""

from core.patching.ltx.merge import merge_patch_plans
from core.patching.models import (
    GeneratedPatch,
    PatchFile,
    PatchIssue,
    PatchOptions,
    PatchPlan,
    PatchTarget,
    VerificationCheck,
    VerificationResult,
)

__all__ = [
    "GeneratedPatch",
    "PatchFile",
    "PatchIssue",
    "PatchOptions",
    "PatchPlan",
    "PatchTarget",
    "VerificationCheck",
    "VerificationResult",
    "merge_patch_plans",
]
