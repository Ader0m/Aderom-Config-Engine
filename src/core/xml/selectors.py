# -*- coding: utf-8 -*-
"""DXML-селекторы по семантической XML-модели.

Модуль не знает о patching: он работает с любым узлом, у которого есть
``tag``, ``attrs``, ``children``, ``parent``, ``is_element`` и ``path``.
Это позволяет использовать селекторы и из XML-резолвера, и из патч-конвейера.
"""

from __future__ import annotations

import re
from typing import List, Optional, TYPE_CHECKING

from core.xml.errors import XmlModelError

if TYPE_CHECKING:  # pragma: no cover - только для типов
    from core.patching.xml.document import XmlDocument, XmlNode


_SAFE_SELECTOR_VALUE_RE = re.compile(r"^[^\s\[\]\"']+$")
_CSS_TAG_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _segment(node: "XmlNode") -> str:
    if not _CSS_TAG_RE.match(node.tag):
        raise XmlModelError(
            "DXML-селектор не поддерживает имя элемента '%s': используйте "
            "XML-файл вместо DXML-скрипта" % node.tag
        )
    text = node.tag
    for attr_name in ("id", "name"):
        value = node.attrs.get(attr_name)
        if value and _SAFE_SELECTOR_VALUE_RE.match(value):
            text += "[%s=%s]" % (attr_name, value)
            break
    return text


def selector_segments(document: "XmlDocument", node: "XmlNode"):
    """CSS-селектор DXML и номер результата (1-based).

    DXML ``query`` ищет потомков ``get_root()``, поэтому корневой элемент
    выбирается отдельно через ``getRoot()``. Возвращает:
    ``(selector, ordinal, is_root)``.
    """
    if not node.is_element:
        raise XmlModelError("Селектор строится только для элемента")

    roots = document.roots()
    if len(roots) == 1 and roots[0] is node:
        return ("", 1, True)

    root = roots[0] if roots else None
    if root is None or not _is_descendant(node, root):
        # Несколько top-level элементов: ищем от синтетического контейнера.
        chain = _ancestor_chain(node)
        if len(chain) <= 1:
            return ("", 1, True)
        segments = [_segment(item) for item in chain[1:]]
        return (" > ".join(segments), 1, False)

    chain = _ancestor_chain(node, stop=root)
    if not chain:
        return ("", 1, True)

    segments = [_segment(item) for item in chain]
    ordinal = _selector_ordinal(root, chain)
    return (" > ".join(segments), ordinal, False)


def _ancestor_chain(
    node: "XmlNode",
    stop: Optional["XmlNode"] = None,
) -> List["XmlNode"]:
    chain: List["XmlNode"] = []
    current = node
    while current is not None and current is not stop:
        if current.is_element:
            chain.append(current)
        current = current.parent
    chain.reverse()
    return chain


def _is_descendant(node: "XmlNode", root: "XmlNode") -> bool:
    current = node.parent
    while current is not None:
        if current is root:
            return True
        current = current.parent
    return False


def _tag_matches(node: "XmlNode", segment: str) -> bool:
    match = re.match(r"^([^\[]+)(?:\[(id|name)=([^\]]+)\])?$", segment)
    if not match:
        return False
    tag, attr_name, attr_value = match.groups()
    if node.tag != tag:
        return False
    if attr_name is None:
        return True
    return node.attrs.get(attr_name) == attr_value


def _selector_ordinal(root: "XmlNode", chain: List["XmlNode"]) -> int:
    """Номер цели среди всех потомков root, подходящих под цепочку.

    ``query`` в DXML возвращает результаты в порядке обхода, поэтому
    номер считается по тому же порядку, а не по индексу среди соседей.
    """
    segments = [_segment(item) for item in chain]
    target = chain[-1] if chain else None
    counter = 0
    result = 0

    def visit(node: "XmlNode", ancestors: List["XmlNode"]):
        nonlocal counter, result
        for child in node.children:
            if not child.is_element:
                continue
            current = ancestors + [child]
            if len(current) >= len(segments):
                tail = current[-len(segments):]
                if all(
                    _tag_matches(actual, segment)
                    for actual, segment in zip(tail, segments)
                ):
                    counter += 1
                    if child is target:
                        result = counter
                        return True
            if visit(child, current):
                return True
        return False

    visit(root, [])
    return max(1, result)
