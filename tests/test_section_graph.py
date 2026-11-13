# -*- coding: utf-8 -*-
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from tests.qt_support import qapp
from modules.base_module.section_graph import (
    SectionGraphWindow,
    _live_graph_state,
    _lost_from_raw,
    _node_key,
    _unique_lost,
)
from sdk.api import GraphEdgeStyle, GraphMatch, GraphNode, SectionGraph

_app = qapp()


class SectionGraphWindowTest(unittest.TestCase):
    def _window(self, calls):
        return SectionGraphWindow(
            lambda path, line: calls.append(("bus", path, line)),
            open_raw=lambda path, line: calls.append(
                ("raw", path, line)
            ),
        )

    def test_resolved_nodes_open_through_bus(self):
        calls = []
        window = self._window(calls)

        root = GraphNode(
            display_name="test",
            file_path=Path("a.ltx"),
            first_match_line=1,
            matches=(GraphMatch("declaration", 1, "[test]"),),
            resolved=True,
        )
        resolved_child = GraphNode(
            display_name="b.ltx",
            file_path=Path("b.ltx"),
            first_match_line=2,
            matches=(GraphMatch("ltx_reference", 2, "test"),),
            resolved=True,
        )
        raw_child = GraphNode(
            display_name="c.ltx",
            file_path=Path("c.ltx"),
            first_match_line=3,
            matches=(GraphMatch("ltx_reference", 3, "test"),),
        )

        window.update_graph(
            SectionGraph(
                section_name="test",
                root=root,
                children=(resolved_child, raw_child),
            )
        )

        for index in range(window.list_widget.count()):
            window._on_item_double_clicked(
                window.list_widget.item(index)
            )

        self.assertEqual(
            calls,
            [
                ("bus", Path("a.ltx"), 1),
                ("bus", Path("b.ltx"), 2),
                ("raw", Path("c.ltx"), 3),
            ],
        )
        window.close()

    def test_sound_rows_open_in_ogg_editor(self):
        opened = []
        window = SectionGraphWindow(
            lambda path, line: None,
        )

        ltx_path = Path("C:/game/gamedata/configs/weapons.ltx")
        sound_path = Path("C:/game/gamedata/sounds/weapon/shot.ogg")
        root = GraphNode(
            display_name="wpn",
            file_path=ltx_path,
            first_match_line=10,
            matches=(GraphMatch("declaration", 10, "[wpn]"),),
        )
        sound = GraphNode(
            display_name="shot.ogg",
            file_path=ltx_path,
            first_match_line=12,
            matches=(
                GraphMatch(
                    "sound_reference",
                    12,
                    "snd_shoot = weapon\\shot",
                    related="snd_shoot",
                ),
            ),
            raw_ref="weapon\\shot",
            target_path=sound_path,
            resolved=True,
        )

        window.update_graph(
            SectionGraph(
                section_name="wpn",
                root=root,
                children=(sound,),
            ),
            edge_styles={
                "sound_reference": GraphEdgeStyle(
                    "Звук",
                    "graph_sound",
                ),
            },
            openers={
                "sound_reference": (
                    lambda node: opened.append(node.target_path)
                ),
            },
        )

        sound_item = window.list_widget.topLevelItem(1)
        self.assertEqual(sound_item.text(0), "[Звук] shot.ogg")
        self.assertIn(str(sound_path), sound_item.toolTip(0))
        self.assertIn("snd_shoot", sound_item.toolTip(0))

        window._on_item_double_clicked(sound_item)
        self.assertEqual(opened, [sound_path])
        window.close()

    def test_asset_rows_show_file_name_and_open_with_shell(self):
        opened = []
        messages = []
        window = SectionGraphWindow(
            lambda path, line: None,
            open_raw=lambda path, line: None,
            open_with_shell=lambda path: (
                opened.append(path) or (True, "")
            ),
            on_message=messages.append,
        )

        ltx_path = Path("C:/game/gamedata/configs/items/weapons.ltx")
        texture_path = Path("C:/game/gamedata/textures/ui/ui_icon.dds")
        physical_texture = Path(
            "C:/MO2/mods/Interface/textures/ui/ui_icon.dds"
        )
        root = GraphNode(
            display_name="weapon",
            file_path=ltx_path,
            first_match_line=10,
            matches=(GraphMatch("declaration", 10, "[weapon]"),),
        )
        texture = GraphNode(
            display_name="ui_icon.dds",
            file_path=ltx_path,
            first_match_line=12,
            matches=(
                GraphMatch(
                    "texture_reference",
                    12,
                    "icons_texture = ui\\ui_icon",
                ),
            ),
            raw_ref="ui\\ui_icon",
            target_path=texture_path,
            physical_path=physical_texture,
        )

        window.update_graph(
            SectionGraph(
                section_name="weapon",
                root=root,
                children=(texture,),
            )
        )

        root_item = window.list_widget.topLevelItem(0)
        texture_item = window.list_widget.topLevelItem(1)

        self.assertEqual(root_item.text(0), "[Объявление] weapons.ltx")
        self.assertEqual(texture_item.text(0), "[Текстура] ui_icon.dds")
        self.assertEqual(texture_item.text(1), "1")
        self.assertEqual(texture_item.text(2), "12")
        self.assertEqual(root_item.text(1), "1")
        self.assertEqual(root_item.text(2), "10")
        self.assertIn(str(texture_path), texture_item.toolTip(0))
        self.assertIn(str(physical_texture), texture_item.toolTip(0))

        window._on_item_double_clicked(texture_item)
        self.assertEqual(opened, [physical_texture])
        self.assertEqual(messages, [])
        window.close()

    def test_visual_node_is_a_stub(self):
        opened = []
        messages = []
        window = SectionGraphWindow(
            lambda path, line: None,
            open_with_shell=lambda path: (
                opened.append(path) or (True, "")
            ),
            on_message=messages.append,
        )

        ltx_path = Path("C:/game/configs/test.ltx")
        model_path = Path("C:/game/gamedata/meshes/trade/model.ogf")
        root = GraphNode(
            display_name="test",
            file_path=ltx_path,
            first_match_line=1,
            matches=(GraphMatch("declaration", 1, "[test]"),),
        )
        visual = GraphNode(
            display_name="model.ogf",
            file_path=ltx_path,
            first_match_line=3,
            matches=(
                GraphMatch(
                    "visual_reference",
                    3,
                    "visual = trade\\model.ogf",
                ),
            ),
            raw_ref="trade\\model.ogf",
            target_path=model_path,
        )

        window.update_graph(
            SectionGraph(
                section_name="test",
                root=root,
                children=(visual,),
            )
        )

        item = window.list_widget.topLevelItem(1)
        self.assertEqual(item.text(0), "[Модель] model.ogf")
        self.assertIn("запакована", item.toolTip(0))

        window._on_item_double_clicked(item)

        self.assertEqual(opened, [])
        self.assertTrue(messages)
        self.assertIn("запакована", messages[0])
        window.close()

    def test_inheritance_rows_show_related_section(self):
        calls = []
        window = SectionGraphWindow(
            lambda path, line: calls.append(("bus", path, line)),
            open_raw=lambda path, line: None,
        )

        ltx_path = Path("C:/game/configs/items/weapons.ltx")
        parent_path = Path("C:/game/configs/items/parents.ltx")
        root = GraphNode(
            display_name="weapon",
            file_path=ltx_path,
            first_match_line=10,
            matches=(GraphMatch("declaration", 10, "[weapon]"),),
        )
        parent = GraphNode(
            display_name="base_weapon",
            file_path=ltx_path,
            first_match_line=10,
            matches=(
                GraphMatch(
                    "inheritance",
                    10,
                    "[weapon]:base_weapon,weapon_common",
                    related="base_weapon",
                    related_is_parent=True,
                ),
            ),
            resolved=True,
            related_file_path=parent_path,
            related_line=42,
            related_resolved=True,
        )

        window.update_graph(
            SectionGraph(
                section_name="weapon",
                root=root,
                children=(parent,),
            )
        )

        item = window.list_widget.topLevelItem(1)
        self.assertEqual(item.text(0), "[Родитель] base_weapon")
        self.assertEqual(item.text(1), "1")
        self.assertEqual(item.text(2), "10")
        tooltip = item.toolTip(0)
        self.assertIn("Родитель: base_weapon", tooltip)
        self.assertIn("base_weapon,weapon_common", tooltip)
        self.assertIn(str(ltx_path), tooltip)
        self.assertIn(str(parent_path), tooltip)

        window._on_item_double_clicked(item)
        self.assertEqual(calls, [("bus", parent_path, 42)])
        window.close()

    def test_child_inheritance_rows_are_labelled_as_children(self):
        window = self._window([])

        ltx_path = Path("C:/game/configs/items/weapons.ltx")
        root = GraphNode(
            display_name="weapon",
            file_path=ltx_path,
            first_match_line=10,
            matches=(GraphMatch("declaration", 10, "[weapon]"),),
        )
        child = GraphNode(
            display_name="wpn_ak74",
            file_path=ltx_path,
            first_match_line=20,
            matches=(
                GraphMatch(
                    "inheritance",
                    20,
                    "[wpn_ak74]:weapon",
                    related="wpn_ak74",
                ),
            ),
        )

        window.update_graph(
            SectionGraph(
                section_name="weapon",
                root=root,
                children=(child,),
            )
        )

        item = window.list_widget.topLevelItem(1)
        self.assertEqual(item.text(0), "[Ребенок] wpn_ak74")
        self.assertIn("Ребенок: wpn_ak74", item.toolTip(0))
        window.close()

    def test_lost_search_only_fills_right_panel(self):
        ltx_path = Path("C:/game/configs/items/weapons.ltx")
        root = GraphNode(
            display_name="weapon",
            file_path=ltx_path,
            first_match_line=1,
            matches=(GraphMatch("declaration", 1, "[weapon]"),),
        )
        sound = GraphNode(
            display_name="shot.ogg",
            file_path=ltx_path,
            first_match_line=2,
            matches=(GraphMatch("sound_reference", 2, "snd_shoot"),),
            target_path=Path("C:/game/gamedata/sounds/weapon/shot.ogg"),
        )
        lost_parent = GraphNode(
            display_name="old_parent",
            file_path=ltx_path,
            first_match_line=3,
            matches=(
                GraphMatch(
                    "inheritance",
                    3,
                    "[other]:old_parent",
                    related="old_parent",
                    related_is_parent=True,
                ),
            ),
        )
        live = SectionGraph(
            section_name="weapon",
            root=root,
            children=(sound,),
        )
        raw = SectionGraph(
            section_name="weapon",
            root=root,
            children=(sound, lost_parent),
        )

        match_keys, xml_files = _live_graph_state(live)
        raw_lost = _lost_from_raw(raw, match_keys, xml_files)
        lost = _unique_lost(
            raw_lost,
            {_node_key(node) for node in live.children},
        )

        # Живой звук не попадает в потери, родитель — попадает.
        self.assertEqual(lost, [lost_parent])

        window = self._window([])
        try:
            window.update_graph(live)
            left_before = window.list_widget.count()
            window.set_lost_nodes(lost)
            self.assertEqual(window.list_widget.count(), left_before)
            self.assertEqual(window.lost_list_widget.count(), 1)
        finally:
            window.close()

    def test_live_inheritance_is_not_lost_after_line_changes(self):
        live_root = GraphNode(
            display_name="weapon",
            file_path=Path("C:/game/configs/system.ltx"),
            first_match_line=1,
            matches=(GraphMatch("declaration", 1, "[weapon]"),),
        )
        live_parent = GraphNode(
            display_name="base",
            file_path=Path("C:/game/configs/system.ltx"),
            first_match_line=10,
            matches=(
                GraphMatch(
                    "inheritance",
                    10,
                    "[weapon]:base",
                    related="base",
                    related_is_parent=True,
                ),
            ),
        )
        raw_parent = GraphNode(
            display_name="base",
            file_path=Path("C:/mods/override.ltx"),
            first_match_line=99,
            matches=(
                GraphMatch(
                    "inheritance",
                    99,
                    "[weapon]:base",
                    related="base",
                    related_is_parent=True,
                ),
            ),
        )

        live = SectionGraph(
            section_name="weapon",
            root=live_root,
            children=(live_parent,),
        )
        raw = SectionGraph(
            section_name="weapon",
            root=live_root,
            children=(raw_parent,),
        )

        match_keys, xml_files = _live_graph_state(live)
        self.assertEqual(
            _lost_from_raw(raw, match_keys, xml_files),
            [],
        )

    def test_asset_with_same_target_is_not_lost(self):
        ltx_path = Path("C:/game/configs/items/weapons.ltx")
        target = Path("C:/game/gamedata/textures/ui/icon.dds")
        live_root = GraphNode(
            display_name="weapon",
            file_path=ltx_path,
            first_match_line=1,
            matches=(GraphMatch("declaration", 1, "[weapon]"),),
        )
        live_texture = GraphNode(
            display_name="icon.dds",
            file_path=ltx_path,
            first_match_line=5,
            matches=(
                GraphMatch(
                    "texture_reference",
                    5,
                    "icons_texture = old\\icon",
                ),
            ),
            raw_ref="old\\icon",
            target_path=target,
        )
        raw_texture = GraphNode(
            display_name="icon.dds",
            file_path=Path("C:/mods/override.ltx"),
            first_match_line=50,
            matches=(
                GraphMatch(
                    "texture_reference",
                    50,
                    "icons_texture = new\\icon",
                ),
            ),
            raw_ref="new\\icon",
            target_path=target,
        )

        live = SectionGraph(
            section_name="weapon",
            root=live_root,
            children=(live_texture,),
        )
        raw = SectionGraph(
            section_name="weapon",
            root=live_root,
            children=(raw_texture,),
        )

        match_keys, xml_files = _live_graph_state(live)
        self.assertEqual(
            _lost_from_raw(raw, match_keys, xml_files),
            [],
        )

    def test_missing_asset_reports_and_opens_declaration(self):
        calls = []
        messages = []
        window = SectionGraphWindow(
            lambda path, line: calls.append(("bus", path, line)),
            open_raw=lambda path, line: calls.append(
                ("raw", path, line)
            ),
            open_with_shell=lambda path: (True, ""),
            on_message=messages.append,
        )

        ltx_path = Path("C:/game/configs/test.ltx")
        root = GraphNode(
            display_name="test",
            file_path=ltx_path,
            first_match_line=1,
            matches=(GraphMatch("declaration", 1, "[test]"),),
        )
        texture = GraphNode(
            display_name="ui_icon.dds",
            file_path=ltx_path,
            first_match_line=3,
            matches=(
                GraphMatch(
                    "texture_reference",
                    3,
                    "icons_texture = no\\such\\icon",
                ),
            ),
            raw_ref="no\\such\\icon",
        )

        window.update_graph(
            SectionGraph(
                section_name="test",
                root=root,
                children=(texture,),
            )
        )

        window._on_item_double_clicked(window.list_widget.topLevelItem(1))
        self.assertTrue(messages)
        self.assertIn("no\\such\\icon", messages[0])
        self.assertEqual(calls, [("raw", ltx_path, 3)])
        window.close()


if __name__ == "__main__":
    unittest.main()
