# -*- coding: utf-8 -*-
"""LTX-контекст для платформенных подсказок.

Разбор LTX/DLTX остаётся в ``core``. Этот модуль — прикладная граница:
UI-слой подсказок получает готовое описание строки и не импортирует
``core`` напрямую.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from core.api.hints import (
    EMPTY_COLORS,
    EMPTY_INCLUDES,
    EMPTY_LOCALIZATION,
    EMPTY_SECTIONS,
    STYLE_COMMENT,
    STYLE_INCLUDE_PATH,
    STYLE_LIST_VALUE,
    STYLE_VALUE,
    parse_key_line,
    parse_section_header,
    token_at,
    tokenize_line,
)

_WINDOW_BACK = 200
_WINDOW_AROUND = 50
_MAX_LOCAL_KEYS = 300


@dataclass(frozen=True)
class LtxLineInfo:
    """Данные LTX-строки, нужные провайдерам подсказок."""

    in_comment: bool = False
    in_string: bool = False
    section: str = ""
    section_kind: str = ""
    section_line: Optional[int] = None
    key: str = ""
    value_kind: str = ""
    local_sections: Tuple[str, ...] = ()
    local_keys: Tuple[Tuple[str, str], ...] = ()


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
    return line[start:end].startswith('"')


def _section_at(line_text_for, line):
    for index in range(int(line), max(-1, int(line) - _WINDOW_BACK), -1):
        text = str(line_text_for(index) or "").strip()
        if not text or text.startswith((";", "//")):
            continue
        header = parse_section_header(text)
        if header is not None:
            kind, name, _parents, _rest = header
            return name, kind, index
    return "", "", None


def _local_sections(line_text_for, line):
    names = []
    seen = set()
    start = max(0, int(line) - _WINDOW_AROUND)
    end = int(line) + _WINDOW_AROUND
    for index in range(start, end + 1):
        text = str(line_text_for(index) or "").strip()
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


def _local_keys(line_text_for, header_line, line):
    result = []
    seen = set()
    start = int(header_line) + 1
    end = int(line) - 1
    if start > end:
        return ()
    for index in range(start, min(end + 1, start + _MAX_LOCAL_KEYS)):
        text = str(line_text_for(index) or "").strip()
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


def build_ltx_line_info(
    line_text_for: Callable[[int], str],
    line: int,
    column: int,
) -> LtxLineInfo:
    """Собрать LTX-метаданные строки для ``HintContext``."""
    line = int(line)
    line_text = str(line_text_for(line) or "")
    column = max(0, min(int(column), len(line_text)))
    tokens = tokenize_line(line_text)
    in_comment = _comment_token(tokens, column) if tokens else False
    in_string = _string_token(line_text, tokens, column) if tokens else False

    section, section_kind, section_line = _section_at(line_text_for, line)
    key = ""
    value_kind = ""
    stripped = line_text.strip()
    if section_line == line and parse_section_header(stripped):
        key = ""
        value_kind = ""
    else:
        parsed = parse_key_line(stripped)
        if parsed:
            key = str(parsed[1] or "")
            value_kind = _value_kind(parsed)

    local_keys = (
        _local_keys(line_text_for, section_line, line)
        if section_line is not None
        else ()
    )
    return LtxLineInfo(
        in_comment=in_comment,
        in_string=in_string,
        section=section,
        section_kind=section_kind,
        section_line=section_line,
        key=key,
        value_kind=value_kind,
        local_sections=_local_sections(line_text_for, line),
        local_keys=local_keys,
    )


__all__ = [
    "EMPTY_COLORS",
    "EMPTY_INCLUDES",
    "EMPTY_LOCALIZATION",
    "EMPTY_SECTIONS",
    "LtxLineInfo",
    "build_ltx_line_info",
]
