# -*- coding: utf-8 -*-
"""Тесты WindowService: сохранение и восстановление раскладки."""

import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from PyQt5.QtWidgets import (
    QDockWidget,
    QMainWindow,
    QSplitter,
    QWidget,
)

from tests.qt_support import qapp
from ui.module_windows import ModuleWindowManager
from ui.platform.window_service import WindowService


class WindowServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qapp()

    def _main_window(self):
        window = QMainWindow()
        splitter = QSplitter()
        splitter.addWidget(QWidget())
        splitter.addWidget(QWidget())
        splitter.setSizes([200, 600])
        window.splitter = splitter
        window.setCentralWidget(splitter)
        dock = QDockWidget("Dock", window)
        dock.setWidget(QWidget())
        window.addDockWidget(1, dock)
        window.resize(900, 600)
        return window

    def test_main_window_layout_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config" / "layout.json"
            service = WindowService(path)
            first = self._main_window()
            service.save_main_window(first)
            self.assertTrue(path.is_file())
            first.close()

            second = self._main_window()
            self.addCleanup(second.close)
            self.assertTrue(service.restore_main_window(second))
            sizes = second.splitter.sizes()
            self.assertEqual(len(sizes), 2)
            self.assertAlmostEqual(
                sizes[0] / float(sum(sizes)),
                200 / 800.0,
                places=2,
            )
            self.assertGreater(second.width(), 0)

    def test_module_window_geometry_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            service = WindowService(Path(d) / "layout.json")
            first = QWidget()
            first.resize(420, 260)
            service.save_module_window("demo.window", "demo", first)

            second = QWidget()
            self.addCleanup(second.close)
            self.assertTrue(
                service.restore_module_window("demo.window", second)
            )
            self.assertEqual(second.size(), first.size())

    def test_module_window_manager_uses_layout(self):
        with tempfile.TemporaryDirectory() as d:
            service = WindowService(Path(d) / "layout.json")
            manager = ModuleWindowManager(layout=service)
            first = QWidget()
            first.resize(380, 240)
            manager.show("demo.window", first, owner="demo")
            first.resize(400, 250)
            manager.close("demo.window", owner="demo")

            second = QWidget()
            self.addCleanup(second.close)
            self.assertTrue(
                manager.show("demo.window", second, owner="demo")
            )
            self.assertEqual(second.size(), first.size())

    def test_missing_layout_file_is_safe(self):
        with tempfile.TemporaryDirectory() as d:
            service = WindowService(Path(d) / "missing.json")
            window = self._main_window()
            self.addCleanup(window.close)
            self.assertFalse(service.restore_main_window(window))
            self.assertFalse(
                service.restore_module_window("missing.window", QWidget())
            )

    def test_content_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "layout.json"
            service = WindowService(path)
            self.assertTrue(
                service.save_state("ogg.main", {"path": "a.ogg"})
            )
            self.assertEqual(
                WindowService(path).load_state("ogg.main"),
                {"path": "a.ogg"},
            )

    def test_newer_layout_version_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "layout.json"
            path.write_text(
                '{"version": 999, "main": {"geometry": "bad"}}',
                encoding="utf-8",
            )
            service = WindowService(path)
            self.assertEqual(service._data["main"], {})


if __name__ == "__main__":
    unittest.main()
