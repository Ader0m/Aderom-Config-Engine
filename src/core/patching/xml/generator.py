# -*- coding: utf-8 -*-
"""Генерация XML-патчей: DXML-Lua и XML-файлы.

Генератор не решает, куда писать. Он создаёт модель файлов патч-мода:

- DXML-Lua: ``gamedata/scripts/modxml_ace_<...>.script``;
- полная копия изменённого XML: ``gamedata/configs/<исходный путь>``;
- новый XML с добавленными элементами: ``gamedata/configs/.../ace_<...>.xml``.

Оригиналы не изменяются. Запись выполняет ``PatchWriter``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from core.patching.xml.diff import XmlChange, XmlDocumentDiff, apply_xml_changes
from core.patching.xml.document import (
    XmlDocument,
    XmlModelError,
    XmlNode,
    parse_xml_document,
    selector_segments,
    serialize_node,
    serialize_xml_document,
)
from core.patching.models import GeneratedPatch, PatchFile, PatchPlan
from core.patching.targets import sanitize_name


class XmlPatchGenerationError(Exception):
    """XML-патч нельзя корректно сгенерировать."""


# ---------------------------------------------------------------- Lua


def _lua_long_string(value: str) -> str:
    """Строка Lua с длинными скобками, устойчивая к ``]]``."""
    text = str(value)
    level = 0
    while ("]" + "=" * level + "]") in text:
        level += 1
    return "[" + "=" * level + "[" + text + "]" + "=" * level + "]"


def _lua_quote(value: str) -> str:
    """Обычная Lua-строка с экранированием."""
    text = str(value)
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    return '"' + text + '"'


def _lua_attr_table(values: Dict[str, str]) -> str:
    parts = []
    for key, value in values.items():
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            lua_key = key
        else:
            lua_key = "[" + _lua_quote(key) + "]"
        parts.append("%s=%s" % (lua_key, _lua_quote(value)))
    return "{" + ", ".join(parts) + "}"


def _root_condition(names: Sequence[str]) -> str:
    conditions = []
    for name in names:
        conditions.append("xml_file_name == %s" % _lua_long_string(name))
    if not conditions:
        return "true"
    return " or ".join(conditions)


def _node_selector(
    document: XmlDocument,
    path: Sequence[Tuple[str, int]],
) -> Tuple[str, int, bool]:
    """Вернуть (css-селектор, номер, корневой ли узел)."""
    node = document.find(path)
    if node is None or not node.is_element:
        raise XmlPatchGenerationError(
            "В документе не найден XML-узел: %s" % (path,)
        )
    return selector_segments(document, node)


def _lua_element_expr(document: XmlDocument, path, indent: str) -> str:
    """Выражение Lua, дающее элемент по пути.

    Возвращает многострочный фрагмент, который заканчивается присваиванием
    переменной ``el``. Если элемент не найден, все операции под условием
    ``if el then`` не выполняются.
    """
    selector, ordinal, is_root = _node_selector(document, path)

    if is_root:
        return "%slocal el = xml_obj:getRoot()\n" % indent

    if not selector:
        return "%slocal el = xml_obj:getRoot()\n" % indent

    return (
        "%slocal res = xml_obj:query(%s)\n"
        "%slocal el = res and res[%d] or nil\n"
        % (indent, _lua_long_string(selector), indent, ordinal)
    )


def _group_changes(changes: Sequence[XmlChange]):
    """Сгруппировать изменения по узлу и виду операции."""
    attrs: Dict[Tuple, Tuple[Dict[str, str], List[str]]] = {}
    texts: Dict[Tuple, XmlChange] = {}
    deletes: List[XmlChange] = []
    replaces: List[XmlChange] = []
    inserts: List[XmlChange] = []

    for change in changes:
        key = tuple(change.path)
        if change.kind in (
            "xml_set_attr",
            "xml_add_attr",
            "xml_delete_attr",
        ):
            set_attrs, remove_attrs = attrs.setdefault(key, ({}, []))
            if change.kind == "xml_delete_attr":
                remove_attrs.append(change.attribute or "")
            else:
                set_attrs[change.attribute or ""] = change.new_value or ""
        elif change.kind == "xml_set_text":
            texts[key] = change
        elif change.kind == "xml_delete_element":
            deletes.append(change)
        elif change.kind == "xml_replace_element":
            replaces.append(change)
        elif change.kind == "xml_add_element":
            inserts.append(change)

    return attrs, texts, deletes, replaces, inserts


def _render_lua_change(
    plan: PatchPlan,
    change: XmlChange,
    document: XmlDocument,
    indent: str = "            ",
) -> List[str]:
    lines: List[str] = []

    if change.kind in ("xml_set_attr", "xml_add_attr", "xml_delete_attr"):
        lines.append(indent + "do")
        lines.append(_lua_element_expr(document, change.path, indent + "    ").rstrip())
        lines.append(indent + "    if el then")
        if change.kind == "xml_delete_attr":
            lines.append(
                indent + "        xml_obj:removeElementAttr(el, {%s})"
                % _lua_quote(change.attribute or "")
            )
        else:
            lines.append(
                indent + "        xml_obj:setElementAttr(el, %s)"
                % _lua_attr_table({change.attribute or "": change.new_value or ""})
            )
        lines.append(indent + "    end")
        lines.append(indent + "end")
        return lines

    if change.kind == "xml_set_text":
        lines.append(indent + "do")
        lines.append(_lua_element_expr(document, change.path, indent + "    ").rstrip())
        lines.append(indent + "    if el then")
        lines.append(
            indent + "        xml_obj:setText(el, %s)"
            % _lua_long_string(change.new_value or "")
        )
        lines.append(indent + "    end")
        lines.append(indent + "end")
        return lines

    if change.kind == "xml_delete_element":
        lines.append(indent + "do")
        lines.append(_lua_element_expr(document, change.path, indent + "    ").rstrip())
        lines.append(indent + "    if el then")
        lines.append(indent + "        xml_obj:removeElement(el)")
        lines.append(indent + "    end")
        lines.append(indent + "end")
        return lines

    if change.kind == "xml_add_element":
        parent_path = tuple(change.parent_path)
        if not parent_path:
            lines.append(indent + "do")
            lines.append(
                indent + "    xml_obj:insertFromXMLString(%s, "
                "xml_obj.xml_table, %d)"
                % (
                    _lua_long_string(change.node_xml),
                    max(1, int(change.position or 1)),
                )
            )
            lines.append(indent + "end")
            return lines

        parent = document.find(parent_path)
        if parent is None:
            raise XmlPatchGenerationError(
                "Не найден родительский XML-узел: %s" % (parent_path,)
            )
        selector, ordinal, is_root = selector_segments(document, parent)
        if is_root:
            parent_expr = "xml_obj:getRoot()"
        else:
            parent_expr = "nil"
            lines.append(indent + "do")
            lines.append(
                "%slocal parent_res = xml_obj:query(%s)"
                % (indent + "    ", _lua_long_string(selector))
            )
            lines.append(
                "%slocal parent = parent_res and parent_res[%d] or nil"
                % (indent + "    ", ordinal)
            )
            lines.append(indent + "    if parent then")
            lines.append(
                indent + "        xml_obj:insertFromXMLString(%s, parent, %d)"
                % (
                    _lua_long_string(change.node_xml),
                    max(1, int(change.position or 1)),
                )
            )
            lines.append(indent + "    end")
            lines.append(indent + "end")
            return lines

        lines.append(indent + "do")
        lines.append(indent + "    local parent = " + parent_expr)
        lines.append(indent + "    if parent then")
        lines.append(
            indent + "        xml_obj:insertFromXMLString(%s, parent, %d)"
            % (
                _lua_long_string(change.node_xml),
                max(1, int(change.position or 1)),
            )
        )
        lines.append(indent + "    end")
        lines.append(indent + "end")
        return lines

    if change.kind == "xml_replace_element":
        # Удаляем старый узел и вставляем новый на его место.
        parent_path = tuple(change.path[:-1])
        lines.append(indent + "do")
        lines.append(_lua_element_expr(document, change.path, indent + "    ").rstrip())
        lines.append(indent + "    if el then")
        if parent_path:
            parent = document.find(parent_path)
            if parent is not None:
                selector, ordinal, is_root = selector_segments(document, parent)
                if is_root:
                    lines.append(indent + "        local parent = xml_obj:getRoot()")
                else:
                    lines.append(
                        "%slocal parent_res = xml_obj:query(%s)"
                        % (indent + "        ", _lua_long_string(selector))
                    )
                    lines.append(
                        "%slocal parent = parent_res and parent_res[%d] or nil"
                        % (indent + "        ", ordinal)
                    )
                lines.append(
                    indent + "        local pos = xml_obj:getElementPosition(el)"
                )
                lines.append(indent + "        xml_obj:removeElement(el)")
                lines.append(indent + "        if parent then")
                lines.append(
                    indent + "            xml_obj:insertFromXMLString(%s, parent, pos or 1)"
                    % _lua_long_string(change.node_xml)
                )
                lines.append(indent + "        end")
            else:
                lines.append(
                    indent + "        local pos = xml_obj:getElementPosition(el)"
                )
                lines.append(indent + "        xml_obj:removeElement(el)")
                lines.append(
                    indent + "        xml_obj:insertFromXMLString(%s, "
                    "xml_obj.xml_table, pos or 1)"
                    % _lua_long_string(change.node_xml)
                )
        else:
            lines.append(indent + "        xml_obj:removeElement(el)")
        lines.append(indent + "    end")
        lines.append(indent + "end")
        return lines

    raise XmlPatchGenerationError(
        "Неизвестное XML-изменение: %s" % change.kind
    )


def _grouped_lua_lines(
    plan: PatchPlan,
    document: XmlDocument,
) -> List[str]:
    attrs, texts, deletes, replaces, inserts = _group_changes(plan.changes)
    lines: List[str] = []
    indent = "            "

    # Сначала операции, которые ищут узлы по старым id/name, и только
    # потом меняем сами атрибуты: иначе селектор потомка может перестать
    # находить родителя.
    for key in sorted(texts, key=str):
        lines.extend(
            _render_lua_change(plan, texts[key], document, indent)
        )

    for change in replaces:
        lines.extend(_render_lua_change(plan, change, document, indent))

    for change in deletes:
        lines.extend(_render_lua_change(plan, change, document, indent))

    for change in sorted(inserts, key=lambda item: item.position):
        lines.extend(_render_lua_change(plan, change, document, indent))

    for key in sorted(attrs, key=str):
        set_attrs, remove_attrs = attrs[key]
        if set_attrs:
            lines.append(indent + "do")
            lines.append(
                _lua_element_expr(document, key, indent + "    ").rstrip()
            )
            lines.append(indent + "    if el then")
            lines.append(
                indent + "        xml_obj:setElementAttr(el, %s)"
                % _lua_attr_table(set_attrs)
            )
            lines.append(indent + "    end")
            lines.append(indent + "end")
        if remove_attrs:
            lines.append(indent + "do")
            lines.append(
                _lua_element_expr(document, key, indent + "    ").rstrip()
            )
            lines.append(indent + "    if el then")
            lines.append(
                indent + "        xml_obj:removeElementAttr(el, {%s})"
                % ", ".join(_lua_quote(name) for name in remove_attrs)
            )
            lines.append(indent + "    end")
            lines.append(indent + "end")

    return lines


def render_dxml(plan: PatchPlan) -> str:
    """Отрендерить DXML-Lua-скрипт по плану."""
    if plan.is_empty:
        return ""

    diff = plan.diff
    document = None
    if isinstance(diff, XmlDocumentDiff):
        document = diff.before

    if document is None:
        # Без before-документа селекторы построить нельзя.
        raise XmlPatchGenerationError(
            "Для DXML-патча нужен исходный XML-документ (diff.before)"
        )

    root_names = list(plan.xml_root_names or ())
    if not root_names:
        root_name = plan.target.root_path or plan.source_path
        root_names = [Path(root_name).name]

    lines: List[str] = [
        "-- =====================================================",
        "-- ACE generated DXML patch",
        "-- Source: %s" % plan.source_path,
        "-- Changes: %d" % len(plan.changes),
    ]

    for issue in tuple(plan.issues) + tuple(plan.warnings):
        lines.append("-- [%s] %s" % (issue.severity, issue.message))

    lines += [
        "-- =====================================================",
        "function on_xml_read()",
        "    RegisterScriptCallback(\"on_xml_read\", "
        "function(xml_file_name, xml_obj)",
        "        if %s then" % _root_condition(root_names),
    ]

    lines.extend(_grouped_lua_lines(plan, document))

    lines += [
        "        end",
        "    end)",
        "end",
    ]
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------- XML files


def _added_change_nodes(changes: Sequence[XmlChange]) -> List[XmlNode]:
    nodes: List[XmlNode] = []
    for change in changes:
        if change.kind != "xml_add_element":
            continue
        try:
            doc = parse_xml_document(change.node_xml)
        except XmlModelError:
            continue
        roots = doc.roots()
        if roots:
            nodes.append(roots[0])
    return nodes


def render_new_xml(plan: PatchPlan, root_tag: str = "") -> str:
    """Создать новый XML-файл с добавленными элементами.

    В файл попадают только изменённые блоки (ключ + значения). Никаких
    служебных комментариев или XML declaration не добавляется, если
    пользователь не запросил обёртку ``root_tag``.
    """
    nodes = _added_change_nodes(plan.changes)
    if not nodes:
        raise XmlPatchGenerationError(
            "В плане нет добавленных XML-элементов"
        )

    body: List[str] = []
    for node in nodes:
        body.append(serialize_node(node))

    if root_tag:
        lines = ["<%s>" % root_tag]
        lines.extend("    " + line for line in "\n".join(body).split("\n"))
        lines.append("</%s>" % root_tag)
        return "\n".join(lines).rstrip() + "\n"

    return "\n".join(body).rstrip() + "\n"


def render_full_xml(plan: PatchPlan) -> str:
    """Полная копия изменённого XML-файла."""
    if plan.after_text:
        return plan.after_text
    diff = plan.diff
    if isinstance(diff, XmlDocumentDiff):
        return serialize_xml_document(diff.after)
    raise XmlPatchGenerationError(
        "Для полной копии XML нужен текст черновика (after_text)"
    )


def default_new_xml_name(plan: PatchPlan, project_tag: str = "") -> str:
    source = Path(plan.source_path)
    tag = sanitize_name(project_tag or "ace", "ace")
    return "%s_ace_%s.xml" % (sanitize_name(source.stem, "xml"), tag)


# ---------------------------------------------------------------- класс


class DxmlGenerator:
    """Генератор XML/DXML-дельты для патч-мода."""

    def __init__(self, resolver_adapter=None, cache=None):
        self.resolver_adapter = resolver_adapter
        self.cache = cache

    def generate(self, plan: PatchPlan) -> GeneratedPatch:
        if plan.target.root_path is None and not plan.is_xml:
            raise XmlPatchGenerationError(
                "Не определён корень загрузки для патча"
            )

        if plan.has_errors:
            raise XmlPatchGenerationError(
                "План содержит ошибки: %s"
                % "; ".join(issue.message for issue in plan.errors())
            )

        if plan.is_empty:
            return GeneratedPatch(plan=plan, files=(), text="", verification=None)

        mode = plan.xml_mode
        if not mode:
            mode = "dxml_lua" if plan.format == "dxml" else "full_copy"

        if mode == "dxml_lua":
            text = render_dxml(plan)
            relative = (
                Path("gamedata")
                / "scripts"
                / (plan.patch_file_rel or Path("modxml_ace.script")).name
            )
            encoding = "utf-8-sig"
            files = (PatchFile(relative, text, encoding),)
        elif mode == "new_file":
            text = render_new_xml(plan, root_tag=plan.xml_new_root_tag)
            relative = Path("gamedata") / "configs" / (
                plan.xml_new_file_rel or plan.patch_file_rel
                or Path("ace_patch.xml")
            )
            encoding = plan.encoding or "windows-1251"
            files = (PatchFile(relative, text, encoding),)
        elif mode == "full_copy":
            text = render_full_xml(plan)
            relative = Path("gamedata") / "configs" / (
                plan.patch_file_rel or Path(_source_name(plan))
            )
            encoding = plan.encoding or "windows-1251"
            files = (PatchFile(relative, text, encoding),)
        elif mode == "mixed":
            files, text = _generate_mixed(plan)
        else:
            raise XmlPatchGenerationError(
                "Неизвестный режим XML-патча: %s" % mode
            )

        return GeneratedPatch(
            plan=plan,
            files=files,
            text=text,
            verification=None,
        )

    def preview(self, plan: PatchPlan) -> str:
        if plan.is_empty:
            return ""
        if plan.xml_mode == "dxml_lua" or plan.format == "dxml":
            return render_dxml(plan)
        if plan.xml_mode == "new_file":
            return render_new_xml(plan, root_tag=plan.xml_new_root_tag)
        if plan.xml_mode == "mixed":
            return _generate_mixed(plan)[1]
        return render_full_xml(plan)


def _generate_mixed(plan: PatchPlan):
    """Полная копия для правок + отдельный XML для добавлений."""
    diff = plan.diff
    if not isinstance(diff, XmlDocumentDiff):
        raise XmlPatchGenerationError(
            "Для mixed-режима нужен исходный XML-diff"
        )

    non_add = [
        change for change in plan.changes
        if change.kind != "xml_add_element"
    ]
    additions = [
        change for change in plan.changes
        if change.kind == "xml_add_element"
    ]

    if not non_add or not additions:
        raise XmlPatchGenerationError(
            "Mixed-режим требует и правок, и добавлений"
        )

    partial = apply_xml_changes(diff.before, non_add)
    full_text = serialize_xml_document(partial)
    new_text = render_new_xml(plan, root_tag=plan.xml_new_root_tag)

    full_rel = Path("gamedata") / "configs" / (
        plan.patch_file_rel or Path(_source_name(plan))
    )
    new_rel = Path("gamedata") / "configs" / (
        plan.xml_new_file_rel or Path("ace_additions.xml")
    )
    encoding = plan.encoding or "windows-1251"

    files = (
        PatchFile(full_rel, full_text, encoding),
        PatchFile(new_rel, new_text, encoding),
    )
    preview = "\n".join(
        [
            "-- Файл: %s" % full_rel,
            full_text.rstrip(),
            "",
            "-- Файл: %s" % new_rel,
            new_text.rstrip(),
            "",
        ]
    )
    return files, preview


def _source_name(plan: PatchPlan) -> str:
    source = Path(plan.source_path)
    return source.name or "ace_patch.xml"
