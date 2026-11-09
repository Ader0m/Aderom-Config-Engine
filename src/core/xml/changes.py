# -*- coding: utf-8 -*-
"""Применение семантических XML-изменений к живому ``xml_obj`` DXML.

Этот модуль находится в XML-слое, поэтому XML-резолвер и патч-проверка
используют его без обратной зависимости ``core.xml`` → ``core.patching``.
"""

from __future__ import annotations

from typing import Sequence, TYPE_CHECKING

from core.xml.dxml import XmlObject
from core.xml.selectors import selector_segments

if TYPE_CHECKING:  # pragma: no cover - только для типов
    from core.patching.xml.diff import XmlChange
    from core.patching.xml.document import XmlDocument


def find_xml_obj_element(
    xml_obj: XmlObject,
    document: "XmlDocument",
    path,
):
    """Найти элемент ``xml_obj`` по пути семантической модели."""
    node = document.find(path)
    if node is None:
        return None
    selector, ordinal, is_root = selector_segments(document, node)
    if is_root:
        return xml_obj.get_root()
    result = xml_obj.query(selector)
    index = max(0, ordinal - 1)
    return result[index] if index < len(result) else None


def apply_changes_to_xml_obj(
    xml_obj: XmlObject,
    document: "XmlDocument",
    changes: Sequence["XmlChange"],
) -> None:
    """Применить изменения плана к живому ``xml_obj`` DXML.

    Атрибуты применяются последними: селекторы потомков используют
    старые ``id``/``name`` и должны найти родителя до их изменения.
    """
    ordered = [
        change for change in changes
        if change.kind not in (
            "xml_set_attr",
            "xml_add_attr",
            "xml_delete_attr",
        )
    ] + [
        change for change in changes
        if change.kind in (
            "xml_set_attr",
            "xml_add_attr",
            "xml_delete_attr",
        )
    ]

    for change in ordered:
        try:
            element = find_xml_obj_element(
                xml_obj,
                document,
                change.path,
            )
        except Exception:
            element = None

        if change.kind in ("xml_set_attr", "xml_add_attr"):
            if element is not None:
                xml_obj.set_element_attr(
                    element,
                    {change.attribute or "": change.new_value or ""},
                )
        elif change.kind == "xml_delete_attr":
            if element is not None:
                xml_obj.remove_element_attr(
                    element,
                    [change.attribute or ""],
                )
        elif change.kind == "xml_set_text":
            if element is not None:
                xml_obj.set_text(element, change.new_value or "")
        elif change.kind == "xml_delete_element":
            if element is not None:
                xml_obj.remove_element(element)
        elif change.kind == "xml_add_element":
            parent = (
                find_xml_obj_element(
                    xml_obj,
                    document,
                    change.parent_path,
                )
                if change.parent_path
                else xml_obj.xml_table
            )
            if parent is not None:
                xml_obj.insert_from_xml_string(
                    change.node_xml,
                    parent,
                    max(1, int(change.position or 1)),
                )
        elif change.kind == "xml_replace_element":
            if element is not None:
                parent = (
                    element.get("parent")
                    if isinstance(element, dict)
                    else None
                )
                position = xml_obj.get_element_position(element) or 1
                xml_obj.remove_element(element)
                if parent is not None:
                    xml_obj.insert_from_xml_string(
                        change.node_xml,
                        parent,
                        position,
                    )
