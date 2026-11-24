# -*- coding: utf-8 -*-
"""Тесты виджета центра операций."""

import unittest

from tests.support import SRC  # noqa: F401

from tests.qt_support import qapp
from ui.operation_center import OperationCenter
from ui.operations import DONE, RUNNING, OperationRegistry, OperationSnapshot


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

        self.assertEqual(center.button.text(), "Операции: 1")
        self.assertTrue(center.isVisible())

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


if __name__ == "__main__":
    unittest.main()
