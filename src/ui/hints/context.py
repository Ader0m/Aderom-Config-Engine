# -*- coding: utf-8 -*-
"""Сборка :class:`sdk.hints.HintContext` из адаптера и цели."""

from __future__ import annotations

from PyQt5.QtGui import QColor

from core.patching.ltx.document import parse_key_line, parse_section_header
from core.patching.ltx.syntax import (
    STYLE_COMMENT,
    STYLE_INCLUDE_PATH,
    STYLE_LIST_VALUE,
    STYLE_VALUE,
    token_at,
    tokenize_line,
)
from sdk.hints import (
    HintContext,
    HintTargetInfo,
    LANGUAGE_LTX,
)

_WINDOW_BACK = 200
_WINDOW_AROUND = 50
_MAX_LOCAL_KEYS = 300


def _rgba(value):
    if value is None:
        return None
    if callable(value):
        try:
            value = value()
        except Exception:
            return None
    if isinstance(value, (tuple, list)) and len(value) >= 3:
        return (
            int(value[0]) & 0xFF,
            int(value[1]) & 0xFF,
            int(value[2]) & 0xFF,
            int(value[3]) & 0xFF if len(value) > 3 else 255,
        )
    if isinstance(value, QColor):
        return (
            value.red() & 0xFF,
            value.green() & 0xFF,
            value.blue() & 0xFF,
            value.alpha() & 0xFF,
        )
    if isinstance(value, str):
        color = QColor(value)
        if color.isValid():
            return (
                color.red() & 0xFF,
                color.green() & 0xFF,
                color.blue() & 0xFF,
                color.alpha() & 0xFF,
            )
    return None


def resolve_target(target) -> HintTargetInfo:
    if callable(target):
        try:
            target = target()
        except Exception:
            target = None
    if target is None:
        return HintTargetInfo()
    if isinstance(target, HintTargetInfo):
        return target
    if isinstance(target, dict):
        return HintTargetInfo(**target)
    return HintTargetInfo(
        surface=getattr(target, "surface", "plain"),
        language=getattr(target, "language", "plain"),
        path=getattr(target, "path", None),
        tab_id=getattr(target, "tab_id", ""),
        role=getattr(target, "role", ""),
        default_color=getattr(target, "default_color", None),
        read_only=getattr(target, "read_only", None),
    )


def _is_ltx(target):
    return str(target.language or "").lower() == LANGUAGE_LTX


def _comment_token(tokens, column):
    token = token_at(tokens, column)
    return token is not None and token[2] == STYLE_COMMENT


def _string_token(line, tokens, column):
    token = token_at(tokens, column)
    if token is None:
        return False
    start, end, style = token
    if style not in (STYLE_VALUE, STYLE_LIST_VALUE, STYLE_INCLUDE_PATH):
        return False
    raw = line[start:end]
    return raw.startswith('"')


def _section_at(adapter, line):
    """Ближайший заголовок секции и его вид."""
    for index in range(int(line), max(-1, int(line) - _WINDOW_BACK), -1):
        text = adapter.line_text(index).strip()
        if not text or text.startswith((";", "//")):
            continue
        header = parse_section_header(text)
        if header is not None:
            kind, name, _parents, _rest = header
            return name, kind, index
    return "", "", None


def _local_sections(adapter, line):
    names = []
    seen = set()
    start = max(0, int(line) - _WINDOW_AROUND)
    end = int(line) + _WINDOW_AROUND
    for index in range(start, end + 1):
        text = adapter.line_text(index).strip()
        if not text or text.startswith((";", "//")):
            continue
        header = parse_section_header(text)
        if header is None:
            continue
        name = header[1]
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return tuple(names)


def _local_keys(adapter, header_line, line):
    result = []
    seen = set()
    start = int(header_line) + 1
    end = int(line) - 1
    if start > end:
        return ()
    for index in range(start, min(end + 1, start + _MAX_LOCAL_KEYS)):
        text = adapter.line_text(index).strip()
        if not text or text.startswith((";", "//", "#")):
            continue
        if parse_section_header(text) is not None:
            break
        parsed = parse_key_line(text)
        if not parsed:
            continue
        _operation, key, value, _has_value = parsed
        key = str(key or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append((key, str(value or "").strip()))
        if len(result) >= _MAX_LOCAL_KEYS:
            break
    return tuple(result)


def _value_kind(parsed):
    if not parsed:
        return ""
    _operation, _key, value, has_value = parsed
    if not has_value:
        return ""
    text = str(value or "").strip()
    if not text:
        return ""
    if '"' in text and text.startswith('"'):
        return "quoted"
    if "," in text:
        return "list"
    return "bare"


def build_context(adapter, target) -> HintContext:
    target = resolve_target(target)
    line = int(adapter.cursor_line())
    column = int(adapter.cursor_column())
    line_text = adapter.line_text(line)
    column = max(0, min(column, len(line_text)))
    prefix = line_text[:column]
    suffix = line_text[column:]

    tokens = tokenize_line(line_text) if _is_ltx(target) else []
    in_comment = _comment_token(tokens, column) if tokens else False
    in_string = (
        _string_token(line_text, tokens, column) if tokens else False
    )

    section = ""
    section_kind = ""
    section_line = None
    key = ""
    value_kind = ""
    local_keys = ()
    if _is_ltx(target):
        section, section_kind, section_line = _section_at(adapter, line)
        stripped = line_text.strip()
        if section_line == line and parse_section_header(stripped):
            key = ""
            value_kind = ""
        else:
            parsed = parse_key_line(stripped)
            if parsed:
                key = str(parsed[1] or "")
                value_kind = _value_kind(parsed)
        if section_line is not None:
            local_keys = _local_keys(adapter, section_line, line)

    local_sections = ()
    provider = getattr(adapter, "local_sections", None)
    if callable(provider):
        try:
            local_sections = tuple(provider() or ())
        except Exception:
            local_sections = ()
    if _is_ltx(target) and not local_sections:
        local_sections = _local_sections(adapter, line)

    editable = bool(adapter.is_editable())
    if target.read_only:
        editable = False

    default_color = _rgba(target.default_color)
    if default_color is None:
        default_color = _rgba(adapter.default_text_color())

    return HintContext(
        target=target,
        adapter_kind=str(getattr(adapter, "kind", "") or ""),
        line=line,
        column=column,
        line_text=line_text,
        prefix=prefix,
        suffix=suffix,
        selected_text=str(adapter.selected_text() or ""),
        default_color=default_color,
        editable=editable,
        in_comment=in_comment,
        in_string=in_string,
        section=section,
        section_kind=section_kind,
        key=key,
        value_kind=value_kind,
        document_revision=int(adapter.document_revision() or 0),
        local_sections=local_sections,
        local_keys=local_keys,
    )
