# -*- coding: utf-8 -*-
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from PyQt5.QtWidgets import QApplication

from modules.base_module.section_graph import SectionGraphWindow
from sdk.api import GraphMatch, GraphNode, SectionGraph

_app = QApplication.instance() or QApplication([])


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

        window._on_item_double_clicked(texture_item)
        self.assertEqual(opened, [texture_path])
        self.assertEqual(messages, [])
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
        visual = GraphNode(
            display_name="model.ogf",
            file_path=ltx_path,
            first_match_line=3,
            matches=(
                GraphMatch(
                    "visual_reference",
                    3,
                    "visual = no\\such\\model",
                ),
            ),
            raw_ref="no\\such\\model",
        )

        window.update_graph(
            SectionGraph(
                section_name="test",
                root=root,
                children=(visual,),
            )
        )

        window._on_item_double_clicked(window.list_widget.topLevelItem(1))
        self.assertTrue(messages)
        self.assertIn("no\\such\\model", messages[0])
        self.assertEqual(calls, [("raw", ltx_path, 3)])
        window.close()


if __name__ == "__main__":
    unittest.main()
