# -*- coding: utf-8 -*-
"""Порт SLAXML из gamedata/scripts/slaxml.script (xray-monolith).

Оставлены наблюдаемые особенности оригинала:
- комментарии отбрасываются;
- текст обрезается по краям (stripWhitespace=true);
- PI всегда сохраняется как ``?xml <содержимое>``;
- атрибуты в simple_dom хранятся без кавычек, simple_xml добавляет их обратно;
- ``#include`` внутри вставляемых XML-строк разрешается через loader.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from pathlib import Path


class SlaxmlError(Exception):
    """Ошибка разбора/сериализации SLAXML."""


@dataclass(frozen=True)
class SourceRef:
    """Владелец строки итогового XML."""

    file: str
    path: Path | None = None
    line: int = 0
    kind: str = "xml"   # "xml" | "patch"


# ---------------------------------------------------------------- regexes (Lua -> Python)

_RE_TAG_WITH_ATTRS = re.compile(
    r"<([A-Za-z_][A-Za-z0-9_.-]*) (.*?)(/?>)",
    re.DOTALL,
)
_RE_QUOTED_ATTR = re.compile(
    r'([A-Za-z_:][A-Za-z0-9_.:-]*)\s*=\s*"(.*?)"',
    re.DOTALL,
)
_RE_BARE_ATTR = re.compile(
    r"([A-Za-z_:][A-Za-z0-9_.:-]*=)([A-Za-z0-9_]+)([ />]?)"
)
_RE_PI = re.compile(r"<\?([A-Za-z_:][A-Za-z0-9_.:-]*)\s?(.*?)\?>", re.DOTALL)
_RE_COMMENT = re.compile(r"<!--(.*?)-->", re.DOTALL)
_RE_CDATA = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)
_RE_START_ELEMENT = re.compile(r"<([A-Za-z_][A-Za-z0-9_.-]*)")
_RE_START_PREFIX = re.compile(r":([A-Za-z_][A-Za-z0-9_.-]*)")
_RE_ATTRIBUTE = re.compile(
    r"\s+([A-Za-z_:][A-Za-z0-9_.:-]*)\s*=\s*"
)
_RE_ATTR_DQ = re.compile(r'"([^<"]*)"')
_RE_ATTR_SQ = re.compile(r"'([^<']*)'")
_RE_CLOSE_ELEMENT = re.compile(r"\s*(/?)>")
_RE_ELEMENT_CLOSE = re.compile(r"</([A-Za-z_][A-Za-z0-9_.-]*)\s*>")
_RE_ELEMENT_CLOSE_PREFIX = re.compile(
    r"</([A-Za-z_][A-Za-z0-9_.-]*):([A-Za-z_][A-Za-z0-9_.-]*)\s*>"
)
_RE_TEXT = re.compile(r"[^<]+", re.DOTALL)
_RE_ENTITY = re.compile(r"&(#?)([0-9A-Za-z]+);")
_RE_INCLUDE_LINE = re.compile(r'"([^"]*)"')

_ENTITY_MAP = {
    "lt": "<",
    "gt": ">",
    "amp": "&",
    "quot": '"',
    "apos": "'",
}

_ESCAPE_MAP = {
    "<": "&lt;",
    ">": "&gt;",
    "&": "&amp;",
    '"': "&quot;",
}


def _one_space(s: str) -> str:
    """Аналог one_space из _g.script: схлопнуть пробелы и обрезать края."""
    return re.sub(r"\s+", " ", s).strip()


def _trim(s: str) -> str:
    return s.strip()


def _unescape(text: str) -> str:
    def repl(m):
        marker, body = m.group(1), m.group(2)
        if marker == "#":
            try:
                if body[:1].lower() == "x":
                    code = int(body[1:], 16)
                else:
                    code = int(body, 10)
            except ValueError:
                return m.group(0)
            return chr(code)
        return _ENTITY_MAP.get(body, m.group(0))

    return _RE_ENTITY.sub(repl, text)


def _esc(text: str) -> str:
    return re.sub(r'[<>&"]', lambda m: _ESCAPE_MAP[m.group(0)], text)


def sanitize(xml: str) -> str:
    """Порт SLAXML:sanitize().

    Нормализует пробелы между атрибутами и добавляет кавычки к атрибутам
    без кавычек, чтобы парсер мог их прочитать.
    """

    def tag_repl(m):
        name, attrs, tail = m.group(1), m.group(2), m.group(3)

        attrs = _RE_QUOTED_ATTR.sub(
            lambda a: '%s="%s" ' % (a.group(1), a.group(2)),
            attrs,
        )
        attrs = _one_space(attrs)
        attrs = _RE_BARE_ATTR.sub(
            lambda a: '%s"%s"%s' % (a.group(1), a.group(2), a.group(3)),
            attrs,
        )
        return _one_space("<" + name + " " + attrs + tail)

    return _RE_TAG_WITH_ATTRS.sub(tag_repl, xml)


# ---------------------------------------------------------------- parser

class SlaxmlParser:
    """Порт SLAXML:parse / SLAXML:simple_dom."""

    def __init__(self, include_loader=None):
        # include_loader(relative_path) -> str | None
        self._include_loader = include_loader

    # -------------------------------------------------- includes
    def _parse_include_line(self, line: str) -> str:
        if not line.startswith("#") or "#include" not in line:
            return line

        m = _RE_INCLUDE_LINE.search(line)
        if not m:
            return line

        include_name = m.group(1)
        if self._include_loader is None:
            return line

        content = self._include_loader(include_name)
        if content is None:
            return line
        return self._parse_includes(content, root_file=False)

    def _parse_includes(self, s: str, root_file: bool) -> str:
        lines = re.split(r"\r\n|\n|\r", s)
        out = []
        for line in lines:
            if not line:
                continue
            out.append(self._parse_include_line(line))
        return "\n".join(out)

    # -------------------------------------------------- parse callbacks
    def simple_dom(self, xml: str, strip_whitespace=True, parse_includes=False):
        """Порт SLAXML:simple_dom()."""
        xml = sanitize(xml or "")
        if parse_includes:
            xml = self._parse_includes(xml, root_file=True)

        doc = {"el": "!doc", "kids": []}
        current = doc
        stack = [doc]

        newlines = [i for i, ch in enumerate(xml) if ch == "\n"]

        def line_of(pos):
            return bisect.bisect_right(newlines, pos) + 1

        state = {"pos": 0, "state": "text", "text_start": 0, "any": False}

        def push_text(value, cdata=False, line=None):
            current["kids"].append(
                {
                    "el": "#" + value,
                    "parent": current,
                    "line": line or line_of(state["text_start"]),
                }
            )

        def finish_text(first):
            if first > state["text_start"]:
                text = xml[state["text_start"]:first]
                if strip_whitespace:
                    text = text.strip()
                if text:
                    push_text(
                        _unescape(text),
                        line=line_of(state["text_start"]),
                    )

        def find_pi():
            m = _RE_PI.match(xml, state["pos"])
            if not m:
                return False
            finish_text(m.start())
            # В SLAXML simple_dom имя PI игнорируется: всегда "?xml " + content.
            doc_pi = {
                "el": "?xml " + _trim(m.group(2)),
                "parent": current,
                "line": line_of(m.start()),
            }
            current["kids"].append(doc_pi)
            state["pos"] = m.end()
            state["text_start"] = state["pos"]
            return True

        def find_comment():
            m = _RE_COMMENT.match(xml, state["pos"])
            if not m:
                return False
            finish_text(m.start())
            # Комментарии отбрасываются.
            state["pos"] = m.end()
            state["text_start"] = state["pos"]
            return True

        def find_cdata():
            m = _RE_CDATA.match(xml, state["pos"])
            if not m:
                return False
            finish_text(m.start())
            push_text(
                m.group(1),
                cdata=True,
                line=line_of(m.start()),
            )
            state["pos"] = m.end()
            state["text_start"] = state["pos"]
            return True

        def start_element():
            nonlocal current
            m = _RE_START_ELEMENT.match(xml, state["pos"])
            if not m:
                return False
            state["any"] = True
            finish_text(m.start())
            line = line_of(m.start())
            state["pos"] = m.end()

            name = m.group(1)
            prefix = None
            m2 = _RE_START_PREFIX.match(xml, state["pos"])
            if m2:
                prefix = name
                name = m2.group(1)
                state["pos"] = m2.end()

            el = {
                "el": "<" + name,
                "kids": [],
                "parent": current,
                "line": line,
                "attrs": {},
            }
            current["kids"].append(el)
            stack.append(el)
            current = el
            el["_prefix"] = prefix
            return True

        def find_attribute():
            m = _RE_ATTRIBUTE.match(xml, state["pos"])
            if not m:
                return False
            name = m.group(1)
            pos2 = m.end()

            # Как в SLAXML:findAttribute: для обычного namespace-префикса
            # в simple_dom попадает локальное имя; xmlns:x сохраняется целиком.
            if ":" in name and not name.startswith("xmlns:"):
                name = name.split(":", 1)[1]

            value = None
            m2 = _RE_ATTR_DQ.match(xml, pos2)
            if m2:
                value = _unescape(m2.group(1))
                state["pos"] = m2.end()
            else:
                m2 = _RE_ATTR_SQ.match(xml, pos2)
                if m2:
                    value = _unescape(m2.group(1))
                    state["pos"] = m2.end()

            if value is None:
                return False

            # Как в simple_dom: атрибут дописывается прямо в строку элемента,
            # без кавычек и без учёта namespace-префикса.
            current["el"] += " " + name + "=" + value
            current.setdefault("attrs", {})[name] = value
            return True

        def close_element():
            nonlocal current
            m = _RE_CLOSE_ELEMENT.match(xml, state["pos"])
            if not m:
                return False
            state["state"] = "text"
            state["pos"] = m.end()
            state["text_start"] = state["pos"]

            if m.group(1) == "/":
                stack.pop()
                current = stack[-1]
            return True

        def find_element_close():
            nonlocal current
            m = _RE_ELEMENT_CLOSE.match(xml, state["pos"])
            if not m:
                m = _RE_ELEMENT_CLOSE_PREFIX.match(xml, state["pos"])
                if not m:
                    return False
                name = m.group(2)
            else:
                name = m.group(1)

            finish_text(m.start())
            stack.pop()
            current = stack[-1]
            state["pos"] = m.end()
            state["text_start"] = state["pos"]
            return True

        while state["pos"] < len(xml):
            if state["state"] == "text":
                if not (
                    find_pi()
                    or find_comment()
                    or find_cdata()
                    or find_element_close()
                ):
                    if start_element():
                        state["state"] = "attributes"
                    else:
                        m = _RE_TEXT.match(xml, state["pos"])
                        if m:
                            state["pos"] = m.end()
                        else:
                            state["pos"] += 1
            else:
                if not find_attribute():
                    if not close_element():
                        raise SlaxmlError(
                            "Was in an element and couldn't find attributes or the close."
                        )

        if not state["any"]:
            raise SlaxmlError("Parsing did not discover any elements")
        if len(stack) > 1:
            raise SlaxmlError("Parsing ended with unclosed elements")

        return doc


# ---------------------------------------------------------------- serializer

def _sanitize_attr_list(s: str) -> str:
    """Порт SLAXML:sanitize_attr_list()."""
    # str_explode_lim(s, " ", 1): первый токен и остаток
    parts = s.split(" ", 1)
    if len(parts) < 2 or _trim(parts[1]) == "":
        return s

    res = parts[0]
    for token in parts[1].split(" "):
        attr = token.split("=", 1)
        if len(attr) > 1:
            res += " " + attr[0] + '="' + attr[1] + '"'
    return res


def simple_xml(node) -> str:
    """Порт SLAXML:simple_xml()."""
    out = []

    def ser(n):
        el = n.get("el", "")
        if not el:
            return
        kind = el[0]

        if kind == "!":
            for kid in n.get("kids", []):
                ser(kid)
        elif kind == "?":
            out.append("<?" + el[1:] + "?>")
        elif kind == "<":
            tag = _sanitize_attr_list(el[1:])
            kids = n.get("kids") or []
            out.append("<" + tag + (">" if kids else "/>"))
            if kids:
                for kid in kids:
                    ser(kid)
                tag_name = tag.split(" ", 1)[0]
                out.append("</" + tag_name + ">")
        elif kind == "#":
            out.append(_esc(el[1:]))

    ser(node)
    return "".join(out)


def assign_origins(dom, origins):
    """Проставить узлам DOM владельца по номеру строки собранного XML."""
    if not origins:
        return

    def walk(node):
        line = node.get("line")
        if line and 1 <= line <= len(origins):
            node["source"] = origins[line - 1]
        for kid in node.get("kids") or []:
            walk(kid)

    walk(dom)


def pretty_dom(dom, indent: str = "    "):
    """Разложить DOM по строкам и вернуть (текст, владельцы строк)."""
    out = []
    line_sources = []

    def add(line, source):
        parts = line.split("\n")
        out.append(parts[0])
        line_sources.append(source)
        for part in parts[1:]:
            out.append(part)
            line_sources.append(None)

    def tag_name(tag):
        return tag.split(" ", 1)[0]

    def source_of(n, inherited=None):
        if n.get("patch_source"):
            return n["patch_source"]
        if n.get("source"):
            return n["source"]
        return inherited

    def inline_source(n, inherited=None):
        # Патч важнее исходного файла: если текст/атрибут изменён DXML,
        # победителем строки считается DXML-скрипт.
        if n.get("patch_source"):
            return n["patch_source"]

        for kid in n.get("kids") or []:
            src = inline_source(kid)
            if src is not None and src.kind == "patch":
                return src

        if n.get("source"):
            return n["source"]

        for kid in n.get("kids") or []:
            src = inline_source(kid)
            if src is not None:
                return src

        return inherited

    def inline(n):
        el = n.get("el", "")
        if not el:
            return ""
        kind = el[0]

        if kind == "?":
            return "<?" + el[1:] + "?>"
        if kind == "#":
            return _esc(el[1:])
        if kind == "<":
            tag = _sanitize_attr_list(el[1:])
            kids = n.get("kids") or []
            if not kids:
                return "<" + tag + "/>"
            body = "".join(inline(k) for k in kids)
            return "<" + tag + ">" + body + "</" + tag_name(tag) + ">"
        return ""

    def block(n, depth, inherited=None):
        el = n.get("el", "")
        if not el:
            return
        kind = el[0]
        pad = indent * depth

        if kind == "?":
            add(pad + "<?" + el[1:] + "?>", source_of(n, inherited))
            return
        if kind == "#":
            add(pad + _esc(el[1:]), source_of(n, inherited))
            return

        tag = _sanitize_attr_list(el[1:])
        kids = n.get("kids") or []
        name = tag_name(tag)
        start_src = source_of(n, inherited)

        if not kids:
            add(pad + "<" + tag + "/>", start_src)
            return

        has_elements = any(
            k.get("el", "").startswith("<") for k in kids
        )
        has_text = any(
            k.get("el", "").startswith("#") for k in kids
        )

        # Только текст или смешанный контент — сохраняем inline,
        # чтобы не добавить пробелы в текстовые узлы.
        if not has_elements or has_text:
            body = "".join(inline(k) for k in kids)
            line_src = inline_source(n) or start_src
            add(pad + "<" + tag + ">" + body + "</" + name + ">", line_src)
            return

        add(pad + "<" + tag + ">", start_src)
        for kid in kids:
            block(kid, depth + 1, start_src)
        add(pad + "</" + name + ">", start_src)

    for kid in dom.get("kids", []):
        block(kid, 0)

    return "\n".join(out), line_sources


def pretty_xml(text: str, indent: str = "    ") -> str:
    """Человекочитаемое форматирование XML после DXML-сериализации.

    Структура и текстовые значения не меняются: добавляются только
    переносы строк и отступы между элементами. Элементы с текстом
    остаются в одну строку, смешанный контент — тоже, чтобы не
    добавлять значимые пробелы в текстовые узлы.
    """
    if not text or not text.strip():
        return text

    parser = SlaxmlParser()
    dom = parser.simple_dom(
        text,
        strip_whitespace=True,
        parse_includes=False,
    )
    return pretty_dom(dom, indent)[0]
