# -*- coding: utf-8 -*-
"""Тесты списка группировки по kind в окне предметов."""

import unittest

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtTest import QTest

from tests.support import SRC  # noqa: F401

from tests.qt_support import qapp
from sdk.api import ItemCatalog, ItemEntry, ItemField
from modules.base_module.item_viewer import ItemViewerWindow

_app = qapp()


def _item(section, kind):
    return ItemEntry(
        section=section,
        fields=(
            ItemField("kind", kind),
            ItemField("inv_name", section),
        ),
        icon_texture=r"ui\x",
        name_text=section,
    )


class ItemKindFilterTest(unittest.TestCase):
    def setUp(self):
        self.window = ItemViewerWindow()
        self.window.resize(1200, 800)
        self.window.set_catalog(
            ItemCatalog(items=(
                _item("a", "i_food"),
                _item("b", "i_food"),
                _item("c", "i_med"),
                _item("d", "w_rifle"),
                _item("e", ""),
            )),
            {},
        )

    def tearDown(self):
        self.window.close()

    def test_kind_list_is_sorted_and_selects_all_by_default(self):
        texts = [
            self.window.kind_list.item(i).text()
            for i in range(self.window.kind_list.count())
        ]
        self.assertEqual(
            texts,
            ["i_food", "i_med", "w_rifle", "(без kind)"],
        )
        self.assertEqual(
            len(self.window.kind_list.selectedItems()),
            len(texts),
        )
        self.assertEqual(self.window.proxy.rowCount(), 5)

    def test_clear_and_select_all(self):
        self.window.kind_list.clearSelection()
        self.assertEqual(self.window.proxy.rowCount(), 0)

        self.window.kind_list.selectAll()
        self.assertEqual(self.window.proxy.rowCount(), 5)

    def test_click_toggles_only_that_kind(self):
        self.window.show()
        _app.processEvents()

        self.window.kind_list.selectAll()
        target = self.window.kind_list.item(1)
        position = self.window.kind_list.visualItemRect(target).center()

        QTest.mouseClick(
            self.window.kind_list.viewport(),
            Qt.LeftButton,
            Qt.NoModifier,
            position,
        )

        selected = {
            item.text()
            for item in self.window.kind_list.selectedItems()
        }
        self.assertNotIn(target.text(), selected)
        self.assertEqual(
            selected,
            {"i_food", "w_rifle", "(без kind)"},
        )

    def test_click_on_empty_area_keeps_selection(self):
        self.window.show()
        _app.processEvents()

        viewport = self.window.kind_list.viewport()
        position = QPoint(10, viewport.height() - 5)
        self.assertIsNone(self.window.kind_list.itemAt(position))

        before = len(self.window.kind_list.selectedItems())
        QTest.mouseClick(
            viewport,
            Qt.LeftButton,
            Qt.NoModifier,
            position,
        )

        self.assertEqual(
            len(self.window.kind_list.selectedItems()),
            before,
        )

    def test_single_kind_filters_items(self):
        self.window.kind_list.clearSelection()
        self.window.kind_list.item(0).setSelected(True)

        sections = [
            self.window._entry_from_index(
                self.window.proxy.index(i, 0)
            ).section
            for i in range(self.window.proxy.rowCount())
        ]
        self.assertEqual(sections, ["a", "b"])


if __name__ == "__main__":
    unittest.main()
