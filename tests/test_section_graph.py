# -*- coding: utf-8 -*-
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from tests.qt_support import qapp
from modules.base_module.section_graph import SectionGraphWindow
from sdk.api import GraphMatch, GraphNode, SectionGraph

_app = qapp()


class SectionGraphWindowTest(unittest.TestCase):
    def _window(self, calls):
        return SectionGraphWindow(
            lambda path, line: calls.append(("bus", path, line)),
            open_raw_at_line=lambda path, line: calls.append(
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
            open_file=opened.append,
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
            )
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
            open_raw_at_line=lambda path, line: None,
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
            open_raw_at_line=lambda path, line: calls.append(
                ("raw", path, line)
            ),
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
        self.assertEqual(item.text(0), "[Наследование] base_weapon")
        self.assertEqual(item.text(1), "1")
        self.assertEqual(item.text(2), "10")
        tooltip = item.toolTip(0)
        self.assertIn("Связь: base_weapon", tooltip)
        self.assertIn("base_weapon,weapon_common", tooltip)
        self.assertIn(str(ltx_path), tooltip)
        self.assertIn(str(parent_path), tooltip)

        window._on_item_double_clicked(item)
        self.assertEqual(calls, [("bus", parent_path, 42)])
        window.close()

    def test_missing_asset_reports_and_opens_declaration(self):
        calls = []
        messages = []
        window = SectionGraphWindow(
            lambda path, line: calls.append(("bus", path, line)),
            open_raw_at_line=lambda path, line: calls.append(
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
