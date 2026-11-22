# -*- coding: utf-8 -*-
"""Документы: DTO, разбор текста движка и контракт DocumentApi."""

from .api import DocumentApi
from .engine_text import (
    EngineTextRun,
    engine_source_text,
    engine_text_lines,
    parse_color_definitions,
    parse_engine_text,
)
from .models import (
    FileView,
    LineSource,
    ResolvedDocumentChange,
    ResolvedDraftInfo,
    ResolvedLineEdit,
    XmlResolveResult,
)

__all__ = [
    "DocumentApi",
    "FileView",
    "LineSource",
    "ResolvedDocumentChange",
    "ResolvedDraftInfo",
    "ResolvedLineEdit",
    "XmlResolveResult",
    "EngineTextRun",
    "engine_source_text",
    "engine_text_lines",
    "parse_color_definitions",
    "parse_engine_text",
]
