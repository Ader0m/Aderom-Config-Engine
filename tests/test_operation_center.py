# -*- coding: utf-8 -*-
"""Тесты виджета центра операций."""

import unittest

from tests.support import SRC  # noqa: F401

from tests.qt_support import qapp
from ui.operation_center import OperationCenter
from ui.operations import DONE, RUNNING, OperationRegistry, OperationSnapshot
from ui.platform.theme import theme_service


class OperationCenterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qapp()

    def test_center_shows_active_count_and_dialog_rows(self):
        registry = OperationRegistry()
        registry.register(
            OperationSnapshot(
                key="demo.running",
                owner="demo",
                title="Сборка",
                state=RUNNING,
                done=1,
                total=3,
                message="шаг 1",
            )
        )
        registry.finish(
            OperationSnapshot(
                key="demo.done",
                owner="demo",
                title="Готово",
                state=DONE,
            )
        )
        center = OperationCenter(registry)
        self.addCleanup(center.deleteLater)
        center.show()
        center.refresh()

        self.assertEqual(center.button.text(), "Сборка 1/3")
        self.assertTrue(center.isVisible())
        self.assertIn(
            theme_service().palette.accent_green,
            center.button.styleSheet(),
        )
        self.assertIn("qlineargradient", center.button.styleSheet())

        center.open()
        dialog = center._dialog
        self.assertIsNotNone(dialog)
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.table.rowCount(), 2)
        self.assertIn("Активных операций: 1", dialog.summary.text())

        dialog.table.selectRow(0)
        self.assertTrue(dialog.cancel_button.isEnabled())
        dialog.table.selectRow(1)
        self.assertFalse(dialog.cancel_button.isEnabled())

    def test_center_keeps_first_started_operation_on_button(self):
        registry = OperationRegistry()
        registry.register(
            OperationSnapshot(
                key="demo.late",
                owner="demo",
                title="Поздняя",
                state=RUNNING,
                done=1,
                total=10,
                started_at=20.0,
            )
        )
        registry.register(
            OperationSnapshot(
                key="demo.early",
                owner="demo",
                title="Ранняя",
                state=RUNNING,
                done=3,
                total=10,
                started_at=10.0,
            )
        )
        center = OperationCenter(registry)
        self.addCleanup(center.deleteLater)
        center.show()
        center.refresh()

        self.assertEqual(center.button.text(), "Ранняя 3/10")
        self.assertIn("активных операций: 2", center.button.toolTip())


if __name__ == "__main__":
    unittest.main()
