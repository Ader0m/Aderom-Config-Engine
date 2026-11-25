# -*- coding: utf-8 -*-
"""Галерея предметов: индекс перетаскивания не переживает сброс модели."""

import unittest

from tests.qt_support import qapp
from tests.support import SRC  # noqa: F401

from PyQt5.QtCore import QPersistentModelIndex, QPoint, QSize, Qt
from PyQt5.QtGui import QMouseEvent, QStandardItem, QStandardItemModel
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from modules.item_browser.item_viewer import ENTRY_ROLE, ItemGalleryView

_app = qapp()


class _View(ItemGalleryView):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.started = []

    def _start_drag(self, index):
        self.started.append(index)


class ItemGalleryDragTest(unittest.TestCase):
    def setUp(self):
        self.model = QStandardItemModel()
        self.view = _View()
        self.view.setModel(self.model)
        self.view.setViewMode(ItemGalleryView.IconMode)
        self.view.setGridSize(QSize(128, 146))
        self.view.resize(600, 400)
        self.view.show()
        self._fill("item", "s")
        _app.processEvents()

    def tearDown(self):
        self.view.close()
        self.view.deleteLater()
        _app.processEvents()

    def _fill(self, prefix, section_prefix):
        for index in range(8):
            item = QStandardItem("%s %d" % (prefix, index))
            item.setData(
                {"section": "%s%d" % (section_prefix, index)},
                ENTRY_ROLE,
            )
            self.model.appendRow(item)
        _app.processEvents()

    def _move_with_left_button(self, point):
        event = QMouseEvent(
            QMouseEvent.MouseMove,
            point,
            Qt.NoButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
        QApplication.sendEvent(self.view.viewport(), event)
        _app.processEvents()

    def test_drag_index_is_persistent_and_cleared_by_model_reset(self):
        rect = self.view.visualRect(self.model.index(0, 0))
        QTest.mousePress(
            self.view.viewport(),
            Qt.LeftButton,
            Qt.NoModifier,
            rect.center(),
        )
        _app.processEvents()

        index = self.view._drag_index
        self.assertIsInstance(index, QPersistentModelIndex)
        self.assertTrue(index.isValid())

        # set_catalog() пересобирает модель тем же способом.
        self.model.clear()
        self._fill("new", "n")

        self.assertFalse(index.isValid())
        self._move_with_left_button(
            rect.center() + QPoint(40, 40)
        )
        self.assertEqual(self.view.started, [])

    def test_drag_still_starts_for_live_index(self):
        rect = self.view.visualRect(self.model.index(0, 0))
        QTest.mousePress(
            self.view.viewport(),
            Qt.LeftButton,
            Qt.NoModifier,
            rect.center(),
        )
        _app.processEvents()

        self._move_with_left_button(
            rect.center() + QPoint(40, 40)
        )

        self.assertEqual(len(self.view.started), 1)
        self.assertEqual(
            self.view.started[0].data(ENTRY_ROLE),
            {"section": "s0"},
        )


if __name__ == "__main__":
    unittest.main()
