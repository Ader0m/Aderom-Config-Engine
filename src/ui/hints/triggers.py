# -*- coding: utf-8 -*-
"""Чистые детекторы контекста подсказок.

Модуль не импортирует Qt и не знает о провайдерах: только разбирает
строку и возвращает диапазоны/префиксы. Для LTX используется общий
построчный токенизатор ядра.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

_DIRECTIVE_RE = re.compile(r"#\s*([A-Za-z_][\w-]*)")
_LOCALIZATION_KEYS = frozenset(
    {"inv_name", "inv_name_short", "description"}
)


@dataclass(frozen=True)
class ColorTrigger:
    content_start: int
    prefix: str


@dataclass(frozen=True)
class SectionTrigger:
    name_start: int
    prefix: str
    operator: str
    bracket: int


@dataclass(frozen=True)
class ParentTrigger:
    name_start: int
    prefix: str
    operator: str
    token_start: int
    exclude: Tuple[str, ...]


@dataclass(frozen=True)
class KeyTrigger:
    name_start: int
    prefix: str
    operator: str


@dataclass(frozen=True)
class IncludeTrigger:
    arg_start: int
    prefix: str
    quoted: bool


@dataclass(frozen=True)
class DirectiveTrigger:
    word_start: int
    prefix: str


@dataclass(frozen=True)
class LocalizationTrigger:
    key: str
    value_start: int
    prefix: str
    quoted: bool
    replace_after: int


def _strip_indent(text):
    stripped = text.lstrip(" \t")
    return len(text) - len(stripped), stripped


def _header_prefix(prefix):
    """Начало заголовка секции и позиция ``[`` (даже если он закрыт)."""
    indent, body = _strip_indent(prefix)
    operator = ""
    bracket = None
    if body.startswith("!!["):
        operator = "!!"
        bracket = indent + 2
    elif body.startswith("!["):
        operator = "!"
        bracket = indent + 1
    elif body.startswith("@["):
        operator = "@"
        bracket = indent + 1
    elif body.startswith("["):
        bracket = indent
    if bracket is None:
        return None
    return indent, operator, bracket


def _comment_start(text):
    in_quote = False
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == '"':
            in_quote = not in_quote
        elif not in_quote:
            if char == ";":
                return index
            if char == "/" and index + 1 < length and text[index + 1] == "/":
                return index
        index += 1
    return -1


def color_trigger(prefix: str) -> Optional[ColorTrigger]:
    low = prefix.lower()
    start = low.rfind("%c[")
    if start < 0:
        return None
    content_start = start + 3
    tail = prefix[content_start:]
    if "]" in tail:
        return None
    return ColorTrigger(content_start=content_start, prefix=tail)


def section_trigger(prefix: str) -> Optional[SectionTrigger]:
    header = _header_prefix(prefix)
    if header is None:
        return None
    _indent, operator, bracket = header
    name_start = bracket + 1
    outside = prefix[name_start:]
    if "]" in outside:
        return None
    return SectionTrigger(
        name_start=name_start,
        prefix=prefix[name_start:],
        operator=operator,
        bracket=bracket,
    )


def parent_trigger(prefix: str) -> Optional[ParentTrigger]:
    header = _header_prefix(prefix)
    if header is None:
        return None
    _indent, _operator, _bracket = header
    close = prefix.find("]")
    if close < 0:
        return None
    colon = prefix.find(":", close + 1)
    if colon < 0:
        return None
    if "=" in prefix[colon + 1:]:
        return None
    if _comment_start(prefix[colon + 1:]) >= 0:
        return None

    tail = prefix[colon + 1:]
    cut = tail.rfind(",")
    token_start = colon + 1 + (cut + 1 if cut >= 0 else 0)
    raw = prefix[token_start:]
    left = len(raw) - len(raw.lstrip(" \t"))
    start = token_start + left
    body = raw.lstrip(" \t")

    exclude = tuple(
        token.strip().lstrip("!").strip().lower()
        for token in tail[: max(0, cut)].split(",")
        if token.strip()
    ) if cut >= 0 else ()

    if body.startswith("!"):
        name_start = start + 1
        operator = "!"
    else:
        name_start = start
        operator = ""
    name_prefix = prefix[name_start:]
    if _comment_start(name_prefix) >= 0:
        return None
    return ParentTrigger(
        name_start=name_start,
        prefix=name_prefix,
        operator=operator,
        token_start=start,
        exclude=tuple(name for name in exclude if name),
    )


def key_trigger(prefix: str) -> Optional[KeyTrigger]:
    _indent, body = _strip_indent(prefix)
    if not body:
        return None
    if body.startswith((";", "//", "#", "[", "![", "!![", "@[")):
        return None
    if body.startswith("%c["):
        return None
    if "=" in body:
        return None
    if _comment_start(body) >= 0:
        return None

    operator = ""
    offset = 0
    if body.startswith("!"):
        operator = "!"
        offset = 1
    elif body.startswith(">"):
        operator = ">"
        offset = 1
    elif body.startswith("<"):
        operator = "<"
        offset = 1

    name = body[offset:]
    if not name:
        return KeyTrigger(
            name_start=len(prefix) - len(name),
            prefix="",
            operator=operator,
        ) if operator else None
    if any(char.isspace() for char in name):
        return None
    name_start = len(prefix) - len(name)
    return KeyTrigger(
        name_start=name_start,
        prefix=name,
        operator=operator,
    )


def include_trigger(prefix: str) -> Optional[IncludeTrigger]:
    _indent, body = _strip_indent(prefix)
    if not body.startswith("#"):
        return None
    match = _DIRECTIVE_RE.match(body)
    if match is None:
        return None
    word = match.group(1).lower()
    if not "include".startswith(word):
        return None
    after = body[match.end():]
    if word != "include":
        if after and not after.startswith((" ", "\t", '"')):
            return None
        if after and not after.lstrip().startswith('"'):
            # #inc| — продолжаем ввод имени директивы
            return None
    base = len(prefix) - len(body)

    quote = body.find('"', match.end())
    if quote >= 0:
        content = body[quote + 1:]
        if '"' in content:
            return None
        return IncludeTrigger(
            arg_start=base + quote + 1,
            prefix=content,
            quoted=True,
        )

    if word != "include":
        return None
    rest = body[match.end():]
    if rest == "":
        return None
    if not rest.strip():
        return IncludeTrigger(
            arg_start=len(prefix),
            prefix="",
            quoted=False,
        )
    left = len(rest) - len(rest.lstrip(" \t"))
    arg_start = base + match.end() + left
    content = prefix[arg_start:]
    if not content:
        return IncludeTrigger(
            arg_start=arg_start,
            prefix="",
            quoted=False,
        )
    if any(char.isspace() for char in content):
        return None
    if _comment_start(content) >= 0:
        return None
    return IncludeTrigger(
        arg_start=arg_start,
        prefix=content,
        quoted=False,
    )


def directive_trigger(prefix: str) -> Optional[DirectiveTrigger]:
    _indent, body = _strip_indent(prefix)
    if not body.startswith("#"):
        return None
    match = _DIRECTIVE_RE.match(body)
    if match is None:
        word = ""
        word_start = len(prefix) - len(body) + 1
    else:
        word = match.group(1).lower()
        word_start = len(prefix) - len(body) + match.start(1)
        if not "include".startswith(word):
            return None
        if word == "include":
            return None
    return DirectiveTrigger(word_start=word_start, prefix=word)


def _find_unquoted_equal(text):
    in_quote = False
    for index, char in enumerate(text):
        if char == '"':
            in_quote = not in_quote
        elif char == "=" and not in_quote:
            return index
    return -1


def localization_trigger(
    prefix: str,
    suffix: str,
) -> Optional[LocalizationTrigger]:
    equal = _find_unquoted_equal(prefix)
    if equal < 0:
        return None
    key = prefix[:equal].strip()
    if key.startswith("!"):
        return None
    if key.lower() not in _LOCALIZATION_KEYS:
        return None

    after = prefix[equal + 1:]
    left = len(after) - len(after.lstrip(" \t"))
    value_start = equal + 1 + left
    content = prefix[value_start:]
    if not content:
        return LocalizationTrigger(
            key=key.lower(),
            value_start=value_start,
            prefix="",
            quoted=False,
            replace_after=0,
        )

    if content.startswith('"'):
        value = content[1:]
        if '"' in value:
            return None
        close = suffix.find('"')
        replace_after = close if close >= 0 else 0
        return LocalizationTrigger(
            key=key.lower(),
            value_start=value_start + 1,
            prefix=value,
            quoted=True,
            replace_after=replace_after,
        )

    if any(char.isspace() for char in content):
        return None
    if _comment_start(content) >= 0:
        return None
    end = len(suffix)
    for marker in (" ", "\t", ";", "//", "\r", "\n"):
        index = suffix.find(marker)
        if 0 <= index < end:
            end = index
    return LocalizationTrigger(
        key=key.lower(),
        value_start=value_start,
        prefix=content,
        quoted=False,
        replace_after=end,
    )
