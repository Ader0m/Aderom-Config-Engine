# -*- coding: utf-8 -*-
"""Порт документов: разбор LTX/XML и точечные правки."""

from core.models import LineSource
from core.patching.ltx.document import (
    declared_sections,
    edit_section_text,
    parse_ltx_document,
)
from core.patching.ltx.multiline import analyze_multiline_edit
from core.patching.xml.document import parse_xml_document

__all__ = [
    "LineSource",
    "analyze_multiline_edit",
    "declared_sections",
    "edit_section_text",
    "parse_ltx_document",
    "parse_xml_document",
]
