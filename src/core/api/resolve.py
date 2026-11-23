# -*- coding: utf-8 -*-
"""Порт резолва LTX/XML и семантической модели."""

from core.resolver.ltx import Assembler, LtxResolverAdapter
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
from core.xml.service import XmlResolveService

__all__ = [
    "SEMANTIC_MODEL_VERSION",
    "Assembler",
    "Diagnostic",
    "Diagnostics",
    "EngineVersions",
    "LtxResolverAdapter",
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
    "XmlResolveService",
]
