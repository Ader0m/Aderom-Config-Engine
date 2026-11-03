# -*- coding: utf-8 -*-
"""DXML-движок и xml_obj API — порт gamedata/scripts/dxml_core.script.

Модуль повторяет наблюдаемое поведение DXML из xray-monolith:
- разбор XML через SLAXML-порт (:mod:`core.xml_slaxml`);
- объект ``xml_obj`` с query/insertFromXMLString/setElementAttr/...;
- ``COnXmlRead``: пропуск локализаций кроме eng/rus, кэш, on_xml_read-колбэки.

Сами DXML-скрипты — Lua. Здесь они представлены Python-портами штатных
``modxml_*.script`` движка. Неизвестные modxml-скрипты не исполняются:
о них пишется предупреждение в лог.
"""

from __future__ import annotations

import re
from pathlib import Path

from .xml_slaxml import (
    SlaxmlParser,
    SourceRef,
    assign_origins,
    simple_xml,
    _one_space,
    _trim,
)


class DxmlError(Exception):
    """Ошибка DXML-обработки."""


# ---------------------------------------------------------------- helpers

def _lua_tostring(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _clamp(value, low, high):
    return max(low, min(high, value))


def _str_explode(s: str, sep: str):
    """Аналог str_explode из _g.script (простое разбиение)."""
    if s == "":
        return [""]
    return s.split(sep)


def _str_explode_lim(s: str, sep: str, limit: int):
    """Аналог str_explode_lim: не более limit разбиений."""
    parts = s.split(sep, limit)
    return parts


def _walk(nodes, callback):
    """xml_walk: рекурсивный обход списка узлов."""
    for node in list(nodes or []):
        if callback(node):
            return True
        if node.get("kids"):
            if _walk(node["kids"], callback):
                return True
    return False


# ---------------------------------------------------------------- xml_obj

class XmlObject:
    """Python-порт таблицы xml_object из dxml_core.script."""

    def __init__(self, xml_file_name, xml_string, xml_table,
                 include_loader=None, log=None, scripts_dir=None,
                 current_patch=None):
        self.xml_file_name = xml_file_name
        self.xml_string = xml_string
        self.xml_table = xml_table
        self._include_loader = include_loader
        self._log = log or (lambda msg: None)
        self._scripts_dir = scripts_dir
        self.current_patch = current_patch
        self._set_parent(xml_table, None)

    # -------------------------------------------------- служебное
    def _set_parent(self, node, parent):
        node["parent"] = parent
        for kid in node.get("kids", []) or []:
            self._set_parent(kid, node)

    def _patch_source(self):
        if not self.current_patch:
            return None
        path = None
        if self._scripts_dir is not None:
            candidate = Path(self._scripts_dir) / (
                self.current_patch + ".script"
            )
            if candidate.is_file():
                path = candidate
        return SourceRef(
            file=self.current_patch,
            path=path,
            kind="patch",
        )

    def _mark_patch(self, node):
        source = self._patch_source()
        if source is None or node is None:
            return
        node["patch_source"] = source
        for kid in node.get("kids") or []:
            self._mark_patch(kid)

    def _mark_node(self, node):
        """Пометить только сам узел (например, изменённый атрибут)."""
        source = self._patch_source()
        if source is not None and node is not None:
            node["patch_source"] = source

    def _error(self, fmt, *args):
        self._log("DXML %s: %s" % (self.xml_file_name, fmt % args))

    # -------------------------------------------------- типы узлов
    def is_pi(self, el):
        return bool(el and el.get("el", "").startswith("?"))

    def is_element(self, el):
        return bool(el and el.get("el", "").startswith("<"))

    def is_text(self, el):
        return bool(el and el.get("el", "").startswith("#"))

    def get_element_name(self, el):
        if not self.is_element(el):
            return None
        return self.get_element(el).get("name")

    # -------------------------------------------------- разбор el-строки
    def get_element(self, el):
        """Разбирает строку ``<name attr=value ...`` в {name, attr}."""
        if not self.is_element(el):
            return None

        body = el["el"][1:]
        parts = _str_explode_lim(body, " ", 1)
        res = {"name": parts[0], "attr": {}}

        if len(parts) > 1:
            for attr in _str_explode(parts[1], " "):
                if not attr:
                    continue
                a = _str_explode(attr, "=")
                if len(a) < 2:
                    res["attr"][a[0]] = True
                else:
                    res["attr"][a[0]] = a[1]
        return res

    def get_root(self):
        kids = self.xml_table.get("kids") or []
        if not kids:
            return self.xml_table

        root_pos = 0
        if self.is_pi(kids[root_pos]):
            root_pos = 1

        if root_pos < len(kids) and kids[root_pos].get("el"):
            if root_pos + 1 < len(kids):
                return self.xml_table
            return kids[root_pos]

        return self.xml_table

    # -------------------------------------------------- поиск
    def check_element(self, el, args):
        data = self.get_element(el)
        if not data:
            return False

        check = {}
        for key, value in data.items():
            if key in args:
                check[key] = value

        found = True
        if args.get("name") and args["name"] != check.get("name"):
            found = False
        elif check.get("attr"):
            for arg_key, arg_value in (args.get("attr") or {}).items():
                if arg_value != check["attr"].get(arg_key):
                    found = False

        return found

    def find_element(self, args, where=None):
        if not args:
            self._error("args query table is empty")
            return []

        if "name" in args or "attr" in args:
            args = [args]

        where_nodes = (
            (where.get("kids") or [])
            if where is not None
            else (self.get_root().get("kids") or [])
        )
        res = []

        def visit(node):
            for arg in args:
                if self.check_element(node, arg):
                    res.append(node)
                    break
            return False

        _walk(where_nodes, visit)
        return res

    def iterate_children(self, el, callback):
        if callback is None:
            self._error("callback not specified for iterateChildren")
            return None
        for i, kid in enumerate(el.get("kids") or [], 1):
            if callback(kid, i):
                return True
        return None

    def find_siblings(self, el, args, first=False):
        parent = el.get("parent") if el else None
        if parent is None:
            return None

        if "name" in args or "attr" in args:
            args = [args]

        res = []
        el_pos = None

        def visit(node, i):
            nonlocal el_pos
            if node is el:
                el_pos = i
                return False
            if el_pos is not None and i > el_pos:
                for arg in args:
                    if self.check_element(node, arg):
                        res.append(node)
                        return bool(first)
            return False

        self.iterate_children(parent, visit)

        if not res:
            self._error("no siblings were found with supplied query")
            return None
        return res

    def find_first_sibling(self, el, args):
        return self.find_siblings(el, args, first=True)

    def get_element_position(self, el):
        parent = el.get("parent") if el else None
        if parent is None:
            return None
        found = None

        def visit(node, i):
            nonlocal found
            if node is el:
                found = i
                return True
            return False

        self.iterate_children(parent, visit)
        return found

    # -------------------------------------------------- сборка элемента
    def _concat_attr(self, attr):
        if not attr:
            return ""
        res = ""
        for key, value in attr.items():
            if value is None:
                continue
            res = res + " " + str(key) + "=" + _lua_tostring(value)
        return res[1:] if res else ""

    def convert_element(self, args):
        if not args or not args.get("name"):
            self._error("you trying to convert empty table")
            return None

        attr = args.get("attr") or {}
        attr_string = self._concat_attr(attr)
        res_el = "<" + str(args["name"])
        if attr_string:
            res_el += " " + attr_string
        return {"el": res_el, "kids": args.get("kids") or []}

    def get_element_attr(self, el):
        data = self.get_element(el)
        return data.get("attr") if data else None

    def set_element_attr(self, el, args):
        if not args:
            self._error("you trying to set empty arguments to element")
            return

        data = self.get_element(el)
        if data is None:
            return
        data.setdefault("attr", {})
        data["attr"].update(args)

        converted = self.convert_element(data)
        if converted:
            el["el"] = converted["el"]
            self._mark_node(el)

    def remove_element_attr(self, el, args):
        if not args:
            self._error("you trying to remove arguments to element")
            return

        remove = {v: True for v in args}
        data = self.get_element(el)
        if data is None:
            return
        for key in list((data.get("attr") or {}).keys()):
            if key in remove:
                del data["attr"][key]

        converted = self.convert_element(data)
        if converted:
            el["el"] = converted["el"]
            self._mark_node(el)

    # -------------------------------------------------- вставка
    def insert_element(self, args, where=None, pos=None):
        if not args:
            self._error("you are trying to insert empty table")
            return None

        if not (args.get("name") or args.get("el")):
            self._error("you are trying to insert non-element node")
            return None

        where = where or self.get_root()
        where.setdefault("kids", [])

        if args.get("name"):
            args = self.convert_element(args)

        pos = _clamp(pos, 1, len(where["kids"]) + 1) if pos else len(where["kids"]) + 1
        self._set_parent(args, where)
        self._mark_patch(args)
        where["kids"].insert(pos - 1, args)
        return args, pos

    def insert_element_before(self, args, el, after=False):
        if not args:
            self._error("you are trying to insert empty table")
            return None
        if not (args.get("name") or args.get("el")):
            self._error("you are trying to insert non-element node")
            return None

        parent = el.get("parent") if el else None
        if parent is None or "kids" not in parent:
            self._error("target element has no parent")
            return None

        pos = self.get_element_position(el)
        if pos is None:
            self._error("failed to insert element %s specified element",
                        "after" if after else "before")
            return None

        if args.get("name"):
            args = self.convert_element(args)

        pos = pos + 1 if after else pos
        self._set_parent(args, parent)
        self._mark_patch(args)
        parent["kids"].insert(pos - 1, args)
        return parent["kids"][pos - 1], pos

    def insert_element_after(self, args, el):
        return self.insert_element_before(args, el, after=True)

    # -------------------------------------------------- вставка XML
    def parse_xml_string(self, xml_string):
        parser = SlaxmlParser(include_loader=self._include_loader)
        dom = parser.simple_dom(
            xml_string,
            strip_whitespace=True,
            parse_includes=True,
        )
        return XmlObject(
            "parseXMLString",
            xml_string,
            dom,
            include_loader=self._include_loader,
            log=self._log,
            scripts_dir=self._scripts_dir,
            current_patch=self.current_patch,
        )

    def insert_from_xml_string(self, xml_string, where=None, pos=None,
                               use_root_node=False):
        if xml_string == "":
            return None

        where = where or self.get_root()
        where.setdefault("kids", [])

        xml_obj = self.parse_xml_string(xml_string)
        root = xml_obj.get_root() if use_root_node else xml_obj.xml_table
        if not root.get("kids"):
            return None

        first_pos = (
            _clamp(pos, 1, len(where["kids"]) + 1)
            if pos
            else len(where["kids"]) + 1
        )
        for i, kid in enumerate(root["kids"], 1):
            self._set_parent(kid, where)
            self._mark_patch(kid)
            where["kids"].insert(first_pos + i - 2, kid)
        return first_pos

    def insert_from_xml_file(self, path, where=None, pos=None,
                             use_root_node=False):
        content = self._include_loader(path) if self._include_loader else None
        if content is None:
            raise DxmlError(
                "DXML ERROR: insertFromXMLFile\nCan't read file by path %s"
                % path
            )
        return self.insert_from_xml_string(content, where, pos, use_root_node)

    def merge_from_xml_string(self, xml_string):
        # В оригинале функция пока ничего не делает.
        return None

    def merge_from_xml_file(self, path):
        content = self._include_loader(path) if self._include_loader else None
        if content is None:
            raise DxmlError(
                "DXML ERROR: mergeFromXMLFile\nCan't read file by path %s"
                % path
            )
        return self.merge_from_xml_string(content)

    # -------------------------------------------------- текст
    def get_text(self, el):
        kids = el.get("kids") or []
        if not kids:
            self._error("element %s doesnt have children", el.get("el"))
            return None
        if len(kids) > 1:
            self._error("element %s is not text only", el.get("el"))
            return None
        if not self.is_text(kids[0]):
            self._error("element %s child is not text node", el.get("el"))
            return None
        return kids[0]["el"][1:]

    def set_text(self, el, text):
        if not self.is_element(el):
            self._error("you are trying to insert text into non-element node")
            return

        kids = el.setdefault("kids", [])
        if not kids:
            kids.append({"el": "#", "parent": el})
        if self.get_text(el) is not None:
            kids[0]["parent"] = el
            kids[0]["el"] = "#" + str(text)
            self._mark_node(kids[0])

    def remove_element(self, el):
        parent = el.get("parent") if el else None
        if parent is None or not parent.get("kids"):
            self._error("target element has no parent")
            return None

        for i, kid in enumerate(parent["kids"]):
            if kid is el:
                removed = parent["kids"].pop(i)
                removed["parent"] = None
                return removed, i + 1
        return None

    # -------------------------------------------------- CSS-подобные запросы
    def query(self, query, where=None):
        query_table = _css_query(query)
        if not query_table:
            raise DxmlError("!DXML ERROR invalid query %s" % query)

        stack = [where or self.get_root()]
        selector = self._selector_space

        for item in query_table:
            if item["type"] == "element":
                new_stack = []
                for el in stack:
                    res = selector(
                        self,
                        {"name": item["value"], "attr": item["attr"]},
                        el,
                    )
                    if not res:
                        break
                    new_stack.extend(res)
                stack = new_stack
                if not stack:
                    return []
            elif item["type"] == "selector":
                selector = self._selectors.get(item["value"])
                if selector is None:
                    self._error(
                        "encountered unknown selector %s in query %s, abort",
                        item["value"],
                        query,
                    )
                    return []
        return stack

    # -------------------------------------------------- селекторы
    @staticmethod
    def _selector_space(xml_obj, args, where):
        return xml_obj.find_element(args, where)

    @staticmethod
    def _selector_child(xml_obj, args, where):
        res = []

        def visit(node, i):
            if xml_obj.check_element(node, args):
                res.append(node)
            return False

        xml_obj.iterate_children(where, visit)
        return res

    @staticmethod
    def _selector_next(xml_obj, args, where):
        r = xml_obj.find_first_sibling(where, args)
        return [r[0]] if r else []

    @staticmethod
    def _selector_after(xml_obj, args, where):
        return xml_obj.find_siblings(where, args) or []

    _selectors = {
        " ": _selector_space.__func__,
        ">": _selector_child.__func__,
        "+": _selector_next.__func__,
        "~": _selector_after.__func__,
    }

    # -------------------------------------------------- удобные функции
    def insert_actor_dialog(self, character_id, dialog_line, pos=None):
        if "character_desc_" not in self.xml_file_name:
            return None

        el = self.query("specific_character[id=%s]" % character_id)
        el = el[0] if el else None
        if el is None:
            return None

        already_has_dialog = False
        actor_break_dialog_pos = None

        def visit(node, i):
            nonlocal already_has_dialog, actor_break_dialog_pos
            if self.get_element_name(node) == "actor_dialog":
                d = self.get_text(node)
                if d == dialog_line:
                    self._error(
                        "character_id %s already has actor_dialog %s",
                        character_id,
                        dialog_line,
                    )
                    already_has_dialog = True
                    return True
                if d and "break_dialog" in d:
                    actor_break_dialog_pos = i
            return False

        self.iterate_children(el, visit)

        if not already_has_dialog:
            pos = (
                _clamp(pos, 1, len(el.get("kids") or []) + 1)
                if pos
                else actor_break_dialog_pos
            )
            result = self.insert_element({"name": "actor_dialog"}, el, pos)
            if result:
                n_el, _n_pos = result
                self.set_text(n_el, dialog_line)
                return result
        return None


# ---------------------------------------------------------------- CSS parser

def _is_alpha_us(c):
    return bool(c) and (c.isalpha() or c == "_")


def _is_word_us(c):
    return bool(c) and (c.isalnum() or c == "_")


def _is_sep(c):
    return bool(c) and (c.isspace() or c in ">+~")


def _css_query(query):
    query_table = []

    def add_element(kind, value):
        query_table.append({"type": kind, "value": value, "attr": {}})

    q = _trim(_one_space(query or ""))
    state = ""
    current_element = ""
    current_selector = ""
    current_attr_key = ""
    current_attr_value = ""

    for i in range(1, len(q) + 2):
        c = q[i - 1] if i <= len(q) else ""

        if state == "":
            if _is_alpha_us(c):
                state = "element"
                current_element += c

        elif state == "element":
            if _is_word_us(c):
                current_element += c
            elif _is_sep(c):
                state = "selector"
                add_element("element", current_element)
                current_selector = c
                current_element = ""
            elif c == "[":
                state = "attr"
                add_element("element", current_element)
                current_element = ""
            elif c == "":
                add_element("element", current_element)

        elif state == "attr":
            if c == "]":
                state = "attr_end"
            elif _is_alpha_us(c):
                state = "attr_key"
                current_attr_key += c

        elif state == "attr_key":
            if c == "=":
                state = "attr_value"
            elif _is_word_us(c):
                current_attr_key += c

        elif state == "attr_value":
            if c == "]":
                state = "attr_end"
                current_attr_value = _trim(current_attr_value)
                if current_attr_value[:1] in ("'", '"'):
                    current_attr_value = current_attr_value[1:]
                if current_attr_value[-1:] in ("'", '"'):
                    current_attr_value = current_attr_value[:-1]
                query_table[-1]["attr"][current_attr_key] = current_attr_value
                current_attr_key = ""
                current_attr_value = ""
            else:
                current_attr_value += c

        elif state == "attr_end":
            if _is_sep(c):
                state = "selector"
                current_selector = c
            elif c == "[":
                state = "attr"

        elif state == "selector":
            if c and c in ">+~":
                current_selector = c
            elif c == "[":
                state = "attr"
            elif _is_alpha_us(c):
                add_element("selector", current_selector)
                state = "element"
                current_element += c

    return query_table


# ---------------------------------------------------------------- штатные DXML-патчи

_FACTION_SPOT_TARGETS = {
    "ui\\map_spots.xml": ("circle_army_spot", "circle_ecolog_spot"),
    "ui\\map_spots_16.xml": ("circle_army_spot", "circle_ecolog_spot"),
}


def _patch_faction_spot_size(xml_file_name, xml_obj, flags):
    for tag in _FACTION_SPOT_TARGETS.get(xml_file_name, ()):
        res = xml_obj.query(tag)
        for el in res:
            xml_obj.set_element_attr(el, {"width": 24, "height": 24})


_PORTRAIT_COVER_TARGETS = {
    "ui\\ui_hud.xml": ("companion_list > slot > icon",),
    "ui\\pda_npc_16.xml": ("contact > icon",),
    "ui\\ui_inventory.xml": ("player > icon", "npc > icon", "upgrade > icon"),
    "ui\\ui_inventory_16.xml": ("player > icon", "npc > icon", "upgrade > icon"),
    "ui\\ui_companion_inv.xml": ("ch_info > icon",),
    "ui\\ui_companion_inv_16.xml": ("ch_info > icon",),
    "ui\\ui_wheel_companion.xml": ("info > icon",),
    "ui\\ui_wheel_companion_16.xml": ("info > icon",),
}


def _patch_portrait_cover(xml_file_name, xml_obj, flags):
    for tag in _PORTRAIT_COVER_TARGETS.get(xml_file_name, ()):
        for el in xml_obj.query(tag):
            xml_obj.set_element_attr(el, {"cover": 1})


_LUA_FATAL_WINDOW = """\
<message_box_lua_fatal_error type="yes_no_copy" x="240" y="256" width="541" height="255" stretch="1">
    <texture>ui_inGame2_message_box</texture>
    <message_text x="40" y="28" width="461" height="150" complex_mode="1">
        <text r="240" g="240" b="240" align="c" vert_align="c" font="letterica18">a</text>
    </message_text>
    <button_copy x="72" y="212" width="127" height="28" check_mode="0">
        <window_name>button_copy</window_name>
        <text font="letterica18">st_disconnect</text>
        <texture>ui_button_ordinary</texture>
    </button_copy>
    <button_yes x="207" y="212" width="127" height="28" check_mode="0">
        <window_name>button_yes</window_name>
        <text font="letterica18">st_reload_save</text>
        <texture>ui_button_ordinary</texture>
    </button_yes>
    <button_no x="342" y="212" width="127" height="28" check_mode="0">
        <window_name>button_no</window_name>
        <text font="letterica18">st_continue</text>
        <texture>ui_button_ordinary</texture>
    </button_no>
</message_box_lua_fatal_error>
"""


def _patch_lua_fatal_window(xml_file_name, xml_obj, flags):
    if xml_file_name in ("ui\\message_box_16.xml", "ui\\message_box.xml"):
        xml_obj.insert_from_xml_string(_LUA_FATAL_WINDOW)


_KEYBIND_UBGL_COMMAND = (
    '<command id="kb_switch_ubgl"\t\t\t\t\texe="custom21"/>'
)
_KEYBIND_IMGUI_COMMAND = (
    '<command id="kb_editor"\t\t\t\t\texe="editor"/>'
)

_KEYBIND_UBGL_STRING_ENG = """\
<string id="kb_switch_ubgl">
    <text>Switch To Under-Barrel Launcher</text>
</string>
"""
_KEYBIND_UBGL_STRING_RUS = """\
<string id="kb_switch_ubgl">
    <text>Подствольник</text>
</string>
"""
_KEYBIND_IMGUI_STRING_ENG = """\
<string id="kb_editor">
    <text>ImGui Editor</text>
</string>
"""
_KEYBIND_IMGUI_STRING_RUS = """\
<string id="kb_editor">
    <text>ImGui редактор</text>
</string>
"""


def _patch_keybinds_ubgl(xml_file_name, xml_obj, flags):
    if xml_file_name == "ui\\ui_keybinding.xml":
        groups = xml_obj.query("group[name=kb_grp_weapons]")
        if groups:
            group = groups[0]
            xml_obj.insert_from_xml_string(
                _KEYBIND_UBGL_COMMAND,
                group,
                len(group.get("kids") or []),
            )

    if xml_file_name == "text\\eng\\ui_st_keybinding.xml":
        nodes = xml_obj.query("string[id=kb_func] > text")
        if nodes and xml_obj.get_text(nodes[0]) is not None:
            xml_obj.set_text(nodes[0], "Sight Aim Mode Toggle")
        xml_obj.insert_from_xml_string(_KEYBIND_UBGL_STRING_ENG)

    if xml_file_name == "text\\rus\\ui_st_keybinding.xml":
        nodes = xml_obj.query("string[id=kb_func] > text")
        if nodes and xml_obj.get_text(nodes[0]) is not None:
            xml_obj.set_text(nodes[0], "Альтернативное прицеливание")
        xml_obj.insert_from_xml_string(_KEYBIND_UBGL_STRING_RUS)


def _patch_keybinds_imgui(xml_file_name, xml_obj, flags):
    if xml_file_name == "ui\\ui_keybinding.xml":
        groups = xml_obj.query("group[name=kb_grp_common]")
        if groups:
            group = groups[0]
            xml_obj.insert_from_xml_string(
                _KEYBIND_IMGUI_COMMAND,
                group,
                len(group.get("kids") or []),
            )

    if xml_file_name == "text\\eng\\ui_st_keybinding.xml":
        xml_obj.insert_from_xml_string(_KEYBIND_IMGUI_STRING_ENG)

    if xml_file_name == "text\\rus\\ui_st_keybinding.xml":
        xml_obj.insert_from_xml_string(_KEYBIND_IMGUI_STRING_RUS)


_BUILTIN_PATCHES = {
    "modxml_fix_faction_spot_size": (_patch_faction_spot_size,),
    "modxml_inject_keybinds": (
        _patch_keybinds_ubgl,
        _patch_keybinds_imgui,
    ),
    "modxml_lua_fatal_window": (_patch_lua_fatal_window,),
    "modxml_portrait_cover": (_patch_portrait_cover,),
}


# ---------------------------------------------------------------- COnXmlRead

class DxmlProcessor:
    """Порт _G.COnXmlRead и диспетчера on_xml_read."""

    def __init__(self, include_loader=None, log=None, scripts_dir=None,
                 builtin_patches=True):
        self._include_loader = include_loader
        self._log = log or (lambda msg: None)
        self._scripts_dir = Path(scripts_dir) if scripts_dir is not None else None
        self._callbacks = []
        self._cache = {}
        self.unsupported_scripts = []

        if builtin_patches:
            self._register_builtins(scripts_dir)

    # -------------------------------------------------- регистрация
    def _register_builtins(self, scripts_dir):
        if scripts_dir is not None:
            scripts_dir = Path(scripts_dir)
            core = scripts_dir / "dxml_core.script"
            if not core.is_file():
                self._log(
                    "DXML: dxml_core.script не найден (%s) — DXML отключён"
                    % scripts_dir
                )
                return

            names = sorted(
                p.stem for p in scripts_dir.glob("modxml_*.script")
                if p.is_file()
            )
        else:
            names = sorted(_BUILTIN_PATCHES)

        for name in names:
            patches = _BUILTIN_PATCHES.get(name)
            if patches is None:
                self.unsupported_scripts.append(name)
                self._log(
                    "DXML: скрипт %s.script не исполняется Python-портом"
                    % name
                )
                continue
            for fn in patches:
                self.register_callback(fn, name=name)

    def register_callback(self, fn, name=None):
        self._callbacks.append((name or getattr(fn, "__name__", "callback"), fn))
        return fn

    @property
    def callbacks_count(self):
        return len(self._callbacks)

    # -------------------------------------------------- обработка
    def process(self, xml_file_name, xml_string):
        """Совместимый API: вернуть только итоговый текст."""
        return self.process_dom(xml_file_name, xml_string)[0]

    def process_dom(self, xml_file_name, xml_string, origins=None):
        """Порт COnXmlRead с сохранением DOM и провенанса строк."""
        if xml_file_name.startswith("text\\") and not (
            xml_file_name.startswith("text\\eng\\")
            or xml_file_name.startswith("text\\rus\\")
        ):
            return xml_string, None

        if xml_file_name == "gameplay\\character_desc_general.xml":
            return xml_string, None

        if xml_file_name in self._cache:
            return self._cache[xml_file_name]

        parser = SlaxmlParser(include_loader=self._include_loader)
        try:
            xml_table = parser.simple_dom(
                xml_string,
                strip_whitespace=True,
                parse_includes=True,
            )
        except Exception as exc:
            self._log("DXML: ошибка разбора %s: %s" % (xml_file_name, exc))
            return xml_string, None

        assign_origins(xml_table, origins or [])

        xml_obj = XmlObject(
            xml_file_name,
            xml_string,
            xml_table,
            include_loader=self._include_loader,
            log=self._log,
            scripts_dir=self._scripts_dir,
        )

        flags = {"cache": False}
        for name, callback in self._callbacks:
            xml_obj.current_patch = name
            try:
                callback(xml_file_name, xml_obj, flags)
            except Exception as exc:
                self._log(
                    "DXML: ошибка патча %s для %s: %s"
                    % (name, xml_file_name, exc)
                )
            finally:
                xml_obj.current_patch = None

        result = simple_xml(xml_table)

        if flags.get("cache"):
            self._cache[xml_file_name] = (result, xml_obj)

        return result, xml_obj
