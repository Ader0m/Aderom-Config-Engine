# -*- coding: utf-8 -*-
"""Порт лексера LTX/DLTX для UI.

UI не импортирует ``core`` напрямую: app-слой отдаёт только стили и
построчный токенизатор.
"""

from core.api.syntax import (
    STYLE_ASSIGN,
    STYLE_COMMENT,
    STYLE_CONSTANT,
    STYLE_DEFAULT,
    STYLE_DIRECTIVE,
    STYLE_INCLUDE_PATH,
    STYLE_KEY,
    STYLE_KEY_DELETE,
    STYLE_KEY_INSERT,
    STYLE_KEY_REMOVE,
    STYLE_LIST_VALUE,
    STYLE_NUMBER,
    STYLE_PARENT,
    STYLE_PARENT_DELETE,
    STYLE_SECTION,
    STYLE_SECTION_DELETE,
    STYLE_SECTION_OVERRIDE,
    STYLE_SECTION_SAFE,
    STYLE_VALUE,
    tokenize_line,
)

__all__ = [
    "STYLE_ASSIGN",
    "STYLE_COMMENT",
    "STYLE_CONSTANT",
    "STYLE_DEFAULT",
    "STYLE_DIRECTIVE",
    "STYLE_INCLUDE_PATH",
    "STYLE_KEY",
    "STYLE_KEY_DELETE",
    "STYLE_KEY_INSERT",
    "STYLE_KEY_REMOVE",
    "STYLE_LIST_VALUE",
    "STYLE_NUMBER",
    "STYLE_PARENT",
    "STYLE_PARENT_DELETE",
    "STYLE_SECTION",
    "STYLE_SECTION_DELETE",
    "STYLE_SECTION_OVERRIDE",
    "STYLE_SECTION_SAFE",
    "STYLE_VALUE",
    "tokenize_line",
]
