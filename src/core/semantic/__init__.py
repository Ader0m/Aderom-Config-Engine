# -*- coding: utf-8 -*-
"""Семантическая модель LTX-резолва.

Модели намеренно не экспортируются из корня ``core``: там уже есть
``SectionSnapshot`` для diff-модели патча. Используйте ``core.semantic.*``.
"""

from core.semantic.models import (
    SEMANTIC_MODEL_VERSION,
    Diagnostic,
    Diagnostics,
    EngineVersions,
    ModRef,
    OverrideChain,
    PropertySnapshot,
    ResolvedRoot,
    RootRef,
    SectionFindPage,
    SectionPage,
    SectionSnapshot,
    SourceRef,
    ValueSource,
    ValueSourceInterner,
)
from core.semantic.query import SectionQueryService

__all__ = [
    "SEMANTIC_MODEL_VERSION",
    "Diagnostic",
    "Diagnostics",
    "EngineVersions",
    "ModRef",
    "OverrideChain",
    "PropertySnapshot",
    "ResolvedRoot",
    "RootRef",
    "SectionFindPage",
    "SectionPage",
    "SectionQueryService",
    "SectionSnapshot",
    "SourceRef",
    "ValueSource",
    "ValueSourceInterner",
]
