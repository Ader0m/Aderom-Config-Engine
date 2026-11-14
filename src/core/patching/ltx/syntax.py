# -*- coding: utf-8 -*-
"""Построчный разбор LTX/DLTX без Qt.

Модуль вынесен из ``ui/lexers.py``: тем же разбором пользуются
подсветка синтаксиса и движок подсказок. Это единственный источник
правил для ``%c``-независимой разметки строки: комментарии ``;``/``//``,
заголовки секций, DLTX-операторы, ``#include``, ключи и значения.

Стили — это числовые метки токенов, а не цвета. UI-слой сопоставляет их
со своими стилями Scintilla и палитрой.
"""

from __future__ import annotations

import re
from typing import List, Optional, Sequence, Tuple

# ---------------------------------------------------------------- LTX styles
STYLE_DEFAULT = 0
STYLE_COMMENT = 1
STYLE_DIRECTIVE = 2
STYLE_SECTION = 3
STYLE_SECTION_OVERRIDE = 4
STYLE_SECTION_DELETE = 5
STYLE_SECTION_SAFE = 6
STYLE_KEY = 7
STYLE_KEY_DELETE = 8
STYLE_KEY_INSERT = 9
STYLE_KEY_REMOVE = 10
STYLE_ASSIGN = 11
STYLE_VALUE = 12
STYLE_NUMBER = 13
STYLE_CONSTANT = 14
STYLE_PARENT = 15
STYLE_PARENT_DELETE = 16
STYLE_INCLUDE_PATH = 17
STYLE_LIST_VALUE = 18

Token = Tuple[int, int, int]

_RE_DIRECTIVE = re.compile(r"#\s*[A-Za-z_][\w-]*")
_RE_NUMBER = re.compile(
    r"[+-]?(?:0[xX][0-9A-Fa-f]+|(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)"
)
_CONSTANTS = {"true", "false", "nil", "null"}

COMMENT_STYLES = frozenset({STYLE_COMMENT})
SECTION_STYLES = frozenset(
    {
        STYLE_SECTION,
        STYLE_SECTION_OVERRIDE,
        STYLE_SECTION_DELETE,
        STYLE_SECTION_SAFE,
    }
)
KEY_STYLES = frozenset(
    {
        STYLE_KEY,
        STYLE_KEY_DELETE,
        STYLE_KEY_INSERT,
        STYLE_KEY_REMOVE,
    }
)
VALUE_STYLES = frozenset(
    {
        STYLE_VALUE,
        STYLE_NUMBER,
        STYLE_CONSTANT,
        STYLE_LIST_VALUE,
        STYLE_INCLUDE_PATH,
    }
)


def _comment_index(line, start):
    """Индекс начала комментария (; или //) вне кавычек, либо -1."""
    in_quote = False
    i = start
    n = len(line)
    while i < n:
        ch = line[i]
        if ch == '"':
            in_quote = not in_quote
        elif not in_quote:
            if ch == ";":
                return i
            if ch == "/" and i + 1 < n and line[i + 1] == "/":
                return i
        i += 1
    return -1


def _find_unquoted(line, char, start, end):
    """Найти символ вне кавычек."""
    in_quote = False
    i = start
    while i < end:
        ch = line[i]
        if ch == '"':
            in_quote = not in_quote
        elif ch == char and not in_quote:
            return i
        i += 1
    return -1


def _has_unquoted_comma(line, start, end):
    """True, если в диапазоне есть запятая вне кавычек (значение-список)."""
    in_quote = False
    i = start
    while i < end:
        ch = line[i]
        if ch == '"':
            in_quote = not in_quote
        elif ch == "," and not in_quote:
            return True
        i += 1
    return False


def _tokenize_value(line, start, end, tokens, list_mode=False):
    """Разметить значение: строки, числа, константы, обычные значения."""
    i = start
    while i < end:
        ch = line[i]

        if ch.isspace():
            i += 1
            continue

        if ch == ",":
            tokens.append((i, i + 1, STYLE_ASSIGN))
            i += 1
            continue

        if ch == '"':
            j = i + 1
            while j < end:
                if line[j] == '"':
                    j += 1
                    break
                j += 1
            tokens.append(
                (i, j, STYLE_LIST_VALUE if list_mode else STYLE_VALUE)
            )
            i = j
            continue

        j = i
        while j < end and not line[j].isspace() and line[j] != ",":
            j += 1
        word = line[i:j]
        low = word.lower()

        if low in _CONSTANTS:
            tokens.append((i, j, STYLE_CONSTANT))
        elif _RE_NUMBER.fullmatch(word):
            tokens.append((i, j, STYLE_NUMBER))
        else:
            tokens.append(
                (i, j, STYLE_LIST_VALUE if list_mode else STYLE_VALUE)
            )
        i = j


def _tokenize_key(line, start, end, tokens):
    """Разметить ключ с учётом DLTX-операторов !, >, <."""
    while start < end and line[start].isspace():
        start += 1
    while end > start and line[end - 1].isspace():
        end -= 1
    if start >= end:
        return

    prefix = line[start]
    if prefix == "!":
        tokens.append((start, end, STYLE_KEY_DELETE))
    elif prefix == ">":
        tokens.append((start, start + 1, STYLE_KEY_INSERT))
        tokens.append((start + 1, end, STYLE_KEY))
    elif prefix == "<":
        tokens.append((start, start + 1, STYLE_KEY_REMOVE))
        tokens.append((start + 1, end, STYLE_KEY))
    else:
        tokens.append((start, end, STYLE_KEY))


def _tokenize_parents(line, start, end, tokens):
    """Разметить список родителей после ``]:`` (с DLTX-удалением !parent)."""
    i = start
    while i < end:
        if line[i] == ",":
            tokens.append((i, i + 1, STYLE_ASSIGN))
            i += 1
            continue

        j = i
        while j < end and line[j] != ",":
            j += 1

        p_start, p_end = i, j
        while p_start < p_end and line[p_start].isspace():
            p_start += 1
        while p_end > p_start and line[p_end - 1].isspace():
            p_end -= 1

        if p_start < p_end:
            if line[p_start] == "!":
                tokens.append((p_start, p_start + 1, STYLE_PARENT_DELETE))
                if p_start + 1 < p_end:
                    tokens.append((p_start + 1, p_end, STYLE_PARENT))
            else:
                tokens.append((p_start, p_end, STYLE_PARENT))
        i = j


def _tokenize_directive(line, start, end, tokens):
    """Разметить #include и его аргументы."""
    match = _RE_DIRECTIVE.match(line, start)
    if match is None:
        tokens.append((start, end, STYLE_DIRECTIVE))
        return

    tokens.append((match.start(), match.end(), STYLE_DIRECTIVE))
    i = match.end()
    while i < end:
        ch = line[i]
        if ch.isspace():
            i += 1
            continue
        if ch == '"':
            j = i + 1
            while j < end:
                if line[j] == '"':
                    j += 1
                    break
                j += 1
            tokens.append((i, j, STYLE_INCLUDE_PATH))
            i = j
            continue

        j = i
        while j < end and not line[j].isspace() and line[j] != '"':
            j += 1
        tokens.append((i, j, STYLE_VALUE))
        i = j


def tokenize_line(line: str) -> List[Token]:
    """Вернуть список ``(start, end, style)`` для одной строки LTX/DLTX."""
    body = line.rstrip("\r\n")
    tokens: List[Token] = []
    n = len(body)

    indent = 0
    while indent < n and body[indent] in " \t":
        indent += 1
    if indent >= n:
        return tokens

    if body.startswith(";", indent) or body.startswith("//", indent):
        tokens.append((indent, n, STYLE_COMMENT))
        return tokens

    if body[indent] == "#":
        comment = _comment_index(body, indent)
        end = comment if comment != -1 else n
        _tokenize_directive(body, indent, end, tokens)
        if comment != -1:
            tokens.append((comment, n, STYLE_COMMENT))
        return tokens

    # Заголовок секции: [sec], ![sec], !![sec], @[sec].
    section_style = None
    if body.startswith("!![", indent):
        section_style = STYLE_SECTION_DELETE
    elif body.startswith("![", indent):
        section_style = STYLE_SECTION_OVERRIDE
    elif body.startswith("@[", indent):
        section_style = STYLE_SECTION_SAFE
    elif body.startswith("[", indent):
        section_style = STYLE_SECTION

    if section_style is not None:
        close = body.find("]", indent)
        if close == -1:
            tokens.append((indent, n, section_style))
            return tokens

        tokens.append((indent, close + 1, section_style))

        rest = close + 1
        comment = _comment_index(body, rest)
        end = comment if comment != -1 else n
        if rest < end and body[rest] == ":":
            tokens.append((rest, rest + 1, STYLE_ASSIGN))
            _tokenize_parents(body, rest + 1, end, tokens)
            rest = end

        # Хвост после заголовка/родителей — комментарий.
        if rest < n:
            tail = body[rest:]
            stripped = tail.lstrip()
            if stripped.startswith(";") or stripped.startswith("//"):
                comment_start = rest + (len(tail) - len(stripped))
                tokens.append((comment_start, n, STYLE_COMMENT))
        return tokens

    # key = value (или просто key / !key).
    comment = _comment_index(body, indent)
    end = comment if comment != -1 else n
    eq = _find_unquoted(body, "=", indent, end)

    if eq == -1:
        _tokenize_key(body, indent, end, tokens)
    else:
        _tokenize_key(body, indent, eq, tokens)
        tokens.append((eq, eq + 1, STYLE_ASSIGN))
        list_mode = _has_unquoted_comma(body, eq + 1, end)
        _tokenize_value(body, eq + 1, end, tokens, list_mode=list_mode)

    if comment != -1:
        tokens.append((comment, n, STYLE_COMMENT))
    return tokens


def token_at(tokens: Sequence[Token], column: int) -> Optional[Token]:
    """Токен, накрывающий колонку, либо ближайший слева."""
    best = None
    for token in tokens:
        start, end, _style = token
        if start <= column < end:
            return token
        if end <= column:
            best = token
        else:
            break
    return best


def style_at(line: str, column: int) -> int:
    """Стиль токена в колонке (``STYLE_DEFAULT``, если пусто)."""
    token = token_at(tokenize_line(line), max(0, int(column)))
    return STYLE_DEFAULT if token is None else token[2]


def find_token(
    tokens: Sequence[Token],
    styles,
    *,
    before_or_at: Optional[int] = None,
) -> Optional[Token]:
    """Первый токен нужного стиля (опционально не правее колонки)."""
    wanted = set(styles)
    for token in tokens:
        if before_or_at is not None and token[0] > before_or_at:
            break
        if token[2] in wanted:
            return token
    return None
