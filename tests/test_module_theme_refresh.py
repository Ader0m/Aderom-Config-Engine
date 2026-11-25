# -*- coding: utf-8 -*-
"""Открытые возможности модуля обновляются по событию смены темы."""

import unittest
from types import SimpleNamespace

from tests.support import SRC  # noqa: F401

from modules.base.module import BaseModule
from modules.item_browser.module import ItemBrowserModule


class BaseModuleThemeRefreshTest(unittest.TestCase):
    def _module(self):
        module = object.__new__(BaseModule)
        module.ctx = SimpleNamespace(log=lambda message: None)
        return module

    def test_theme_change_refreshes_section_graph(self):
        module = self._module()
        calls = []
        module._section_graph = SimpleNamespace(
            refresh_theme=lambda: calls.append("graph")
        )

        module._on_theme_changed()

        self.assertEqual(calls, ["graph"])

    def test_theme_change_isolates_feature_errors(self):
        module = self._module()
        calls = []

        def fail():
            raise RuntimeError("не удалось перекрасить")

        module._section_graph = SimpleNamespace(refresh_theme=fail)

        module._on_theme_changed()

        self.assertEqual(calls, [])


class ItemBrowserModuleThemeRefreshTest(unittest.TestCase):
    def test_theme_change_refreshes_item_viewer(self):
        module = object.__new__(ItemBrowserModule)
        module.ctx = SimpleNamespace(log=lambda message: None)
        calls = []
        module._item_viewer = SimpleNamespace(
            refresh_theme=lambda: calls.append("items")
        )

        module._on_theme_changed()

        self.assertEqual(calls, ["items"])


if __name__ == "__main__":
    unittest.main()
