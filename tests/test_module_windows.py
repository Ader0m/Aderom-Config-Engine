# -*- coding: utf-8 -*-
"""Тесты менеджера окон модулей."""

import unittest

from tests.support import SRC  # noqa: F401
from tests.qt_support import qapp

from PyQt5.QtWidgets import QWidget

from ui.module_windows import ModuleWindowManager


class ModuleWindowManagerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qapp()

    def setUp(self):
        self.manager = ModuleWindowManager()
        self.main = QWidget()
        self.manager.bind_main_window(self.main)

    def test_show_binds_and_closes_window(self):
        window = QWidget()
        self.manager.show("test.window", window)

        self.assertTrue(window.isVisible())
        self.assertIs(window.parent(), self.main)

        self.manager.close("test.window")
        self.assertFalse(window.isVisible())

    def test_close_unknown_key_is_noop(self):
        self.assertIsNone(self.manager.close("missing.window"))

    def test_close_all_closes_every_window(self):
        first = QWidget()
        second = QWidget()
        self.manager.show("first", first)
        self.manager.show("second", second)

        self.manager.close_all()

        self.assertFalse(first.isVisible())
        self.assertFalse(second.isVisible())

    def test_replacing_window_with_same_key_closes_previous(self):
        first = QWidget()
        second = QWidget()
        self.manager.show("same", first)
        self.manager.show("same", second)

        self.assertFalse(first.isVisible())
        self.assertTrue(second.isVisible())


if __name__ == "__main__":
    unittest.main()
