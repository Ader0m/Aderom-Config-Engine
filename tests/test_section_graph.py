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


if __name__ == "__main__":
    unittest.main()
