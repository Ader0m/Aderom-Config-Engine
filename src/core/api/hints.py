# -*- coding: utf-8 -*-
"""Порт ядра для разбора LTX-строк в подсказках.

UI-слой не импортирует ``core`` напрямую. Прикладной слой использует
этот порт, а UI получает готовое описание через ``app.hints``.
"""

from core.catalogs import (
    EMPTY_COLORS,
    EMPTY_INCLUDES,
    EMPTY_LOCALIZATION,
    EMPTY_SECTIONS,
)
from core.patching.ltx.document import parse_key_line, parse_section_header
from core.patching.ltx.syntax import (
    STYLE_COMMENT,
    STYLE_INCLUDE_PATH,
    STYLE_LIST_VALUE,
    STYLE_VALUE,
    token_at,
    tokenize_line,
)

__all__ = [
    "EMPTY_COLORS",
    "EMPTY_INCLUDES",
    "EMPTY_LOCALIZATION",
    "EMPTY_SECTIONS",
    "STYLE_COMMENT",
    "STYLE_INCLUDE_PATH",
    "STYLE_LIST_VALUE",
    "STYLE_VALUE",
    "parse_key_line",
    "parse_section_header",
    "token_at",
    "tokenize_line",
]
