# -*- coding: utf-8 -*-
"""Общее экранирование XML-текста."""

from __future__ import annotations

import re


_XML_ESCAPE = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
}


def escape_attr(value) -> str:
    """Экранировать значение атрибута."""
    return re.sub(
        r"[&<>\"]",
        lambda match: _XML_ESCAPE[match.group(0)],
        str(value),
    )


def escape_text(value) -> str:
    """Экранировать текстовый узел."""
    return re.sub(
        r"[&<>]",
        lambda match: _XML_ESCAPE[match.group(0)],
        str(value),
    )
