# -*- coding: utf-8 -*-
"""Тесты менеджера окон модулей."""

import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401
from tests.qt_support import qapp

from PyQt5.QtWidgets import QWidget

from sdk.contracts.events import PLATFORM_OWNER
from ui.module_windows import ModuleWindowManager
from ui.platform.window_service import WindowService


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
        # Окно модуля не должно быть owned-окном главного: иначе оно
        # всегда остаётся поверх него в оконном менеджере Windows.
        self.assertIsNone(window.parent())
        self.assertTrue(window.isWindow())

        self.manager.close("test.window")
        self.assertFalse(window.isVisible())

    def test_focus_known_window_returns_true(self):
        window = QWidget()
        self.manager.show("test.window", window)
        window.hide()

        self.assertTrue(self.manager.focus("test.window"))
        self.assertTrue(window.isVisible())
        window.close()

    def test_focus_unknown_window_returns_false(self):
        self.assertFalse(self.manager.focus("missing.window"))

    def test_close_unknown_key_is_noop(self):
        self.assertFalse(self.manager.close("missing.window"))

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

    def test_close_owner_closes_only_matching_windows(self):
        first = QWidget()
        second = QWidget()
        self.manager.show("first", first, owner="demo")
        self.manager.show("second", second, owner="other")
        self.assertEqual(self.manager.owner_of("first"), "demo")

        self.manager.close_owner("demo")

        self.assertFalse(first.isVisible())
        self.assertTrue(second.isVisible())
        self.assertEqual(self.manager.owner_of("first"), "")

    def test_module_cannot_close_foreign_window(self):
        window = QWidget()
        self.manager.show("demo.window", window, owner="demo")

        self.assertFalse(self.manager.close("demo.window", owner="other"))
        self.assertTrue(window.isVisible())
        self.assertTrue(self.manager.close("demo.window", owner="demo"))
        self.assertFalse(window.isVisible())

    def test_module_cannot_replace_foreign_window(self):
        first = QWidget()
        second = QWidget()
        self.manager.show("demo.window", first, owner="demo")

        self.assertFalse(
            self.manager.show("demo.window", second, owner="other")
        )
        self.assertTrue(first.isVisible())
        self.assertFalse(second.isVisible())
        self.assertEqual(self.manager.owner_of("demo.window"), "demo")

    def test_platform_owner_can_replace_and_close_any_window(self):
        first = QWidget()
        second = QWidget()
        self.manager.show("demo.window", first, owner="demo")

        self.assertTrue(
            self.manager.show(
                "demo.window",
                second,
                owner=PLATFORM_OWNER,
            )
        )
        self.assertFalse(first.isVisible())
        self.assertTrue(second.isVisible())
        self.assertTrue(
            self.manager.close(
                "demo.window",
                owner=PLATFORM_OWNER,
            )
        )
        self.assertFalse(second.isVisible())

    def test_user_close_saves_geometry_and_forgets_window(self):
        with tempfile.TemporaryDirectory() as d:
            layout = WindowService(Path(d) / "layout.json")
            manager = ModuleWindowManager(layout=layout)
            window = QWidget()
            window.resize(360, 240)
            manager.show(
                "demo.window",
                window,
                owner="demo",
                state_key="demo.state",
            )
            window.close()
            self.assertNotIn("demo.window", manager._windows)
            entry = layout.load_dock("demo.window")
            self.assertEqual(entry, {})
            self.assertIn("demo.window", layout.module_window_keys())
            self.assertEqual(
                layout._data["windows"]["demo.window"]["state_key"],
                "demo.state",
            )
            self.assertTrue(
                layout.restore_module_window(
                    "demo.window",
                    QWidget(),
                )
            )


if __name__ == "__main__":
    unittest.main()
