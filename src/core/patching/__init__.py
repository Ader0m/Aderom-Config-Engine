# -*- coding: utf-8 -*-
"""Патч-конвейер: план, генерация, проверка, слияние и запись.

Форматные входы:

- :class:`core.patching.ltx.LtxPatchService`;
- :class:`core.patching.xml.XmlPatchService`.

Общие модели и запись патча не зависят от формата.
"""

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
    "GeneratedPatch",
    "PatchFile",
    "PatchIssue",
    "PatchOptions",
    "PatchPlan",
    "PatchResult",
    "PatchTarget",
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
