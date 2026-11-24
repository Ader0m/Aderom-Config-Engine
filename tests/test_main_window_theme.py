# -*- coding: utf-8 -*-
"""Интеграционный тест: MainWindow перекрашивает вкладки и дерево."""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import SRC  # noqa: F401

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QTreeWidgetItem

from sdk import FileOpenHandler
from sdk.contracts.events import Events
from tests.qt_support import qapp
from ui.bus import EventBus
from ui.main_window import MainWindow
from ui.platform_api import PlatformApi
from ui.platform.theme import DEFAULT_PALETTE, apply_theme


class _ChoiceHandler(FileOpenHandler):
    priority = 10

    def __init__(self, name, handler_id):
        self.name = name
        self.handler_id = handler_id
        self.calls = []

    def can_open(self, path):
        return True

    def open(self, path, api):
        self.calls.append(Path(path))
        return True


class MainWindowThemeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qapp()

    def _window(self, root):
        api = PlatformApi(
            EventBus(),
            SimpleNamespace(register=lambda *a, **k: None),
            None,
            SimpleNamespace(
                tree_query=SimpleNamespace(
                    sort_nodes=lambda nodes: nodes,
                ),
            ),
            SimpleNamespace(
                projects_dir=Path(root) / "projects",
                app_dir=Path(root),
            ),
        )
        window = MainWindow(api)
        self.addCleanup(window.close)
        self.addCleanup(lambda: apply_theme(self.app))
        return window

    def test_apply_theme_repaints_tabs_and_tree(self):
        with tempfile.TemporaryDirectory() as d:
            window = self._window(d)
            editor = window.editor.ensure_default_tab()
            item = QTreeWidgetItem(window.file_tree)
            item.setData(0, Qt.UserRole, "a.ltx")
            window.file_tree.addTopLevelItem(item)
            window.file_tree._path_to_item["a.ltx"] = item
            window.file_tree.highlight(["a.ltx"])

            custom = replace(
                DEFAULT_PALETTE,
                bg_main="#112233",
                tree_highlight="#445566",
            )
            seen = []
            window.api.events.subscribe(
                Events.UI_THEME_CHANGED,
                seen.append,
            )
            apply_theme(self.app, custom)

            self.assertEqual(seen, [custom])

            self.assertEqual(
                item.background(0).color().name(),
                "#445566",
            )
            self.assertEqual(
                editor.editor.paper().name(),
                "#112233",
            )

    def test_choose_open_handler_returns_key_of_selected_label(self):
        with tempfile.TemporaryDirectory() as d:
            window = self._window(d)
            options = (
                ("key-one", "Handler(key=key-one)"),
                ("key-two", "Handler(key=key-two)"),
            )
            with patch(
                "ui.main_window.QInputDialog.getItem",
                return_value=(options[1][1], True),
            ):
                chosen = window._choose_open_handler(
                    Path("a.ltx"),
                    "view",
                    options,
                )
            self.assertEqual(chosen, "key-two")

    def test_router_chooser_uses_unique_labels(self):
        with tempfile.TemporaryDirectory() as d:
            window = self._window(d)
            first = _ChoiceHandler("first", "first")
            second = _ChoiceHandler("second", "second")
            window.api.router.register(first, owner="mod_same")
            window.api.router.register(second, owner="mod_same")
            seen = []

            def choose(parent, title, prompt, items, current, editable):
                seen.extend(items)
                return items[1], True

            with patch(
                "ui.main_window.QInputDialog.getItem",
                side_effect=choose,
            ):
                self.assertTrue(
                    window.api.router.open(Path("a.ltx"), window.api)
                )

            self.assertEqual(len(seen), 2)
            self.assertEqual(len(set(seen)), 2)
            self.assertEqual(first.calls, [])
            self.assertEqual(second.calls, [Path("a.ltx")])


if __name__ == "__main__":
    unittest.main()
