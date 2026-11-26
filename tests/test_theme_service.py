# -*- coding: utf-8 -*-
"""Тесты ThemeService и токенов темы."""

import unittest
from dataclasses import replace

from tests.support import SRC  # noqa: F401
from tests.qt_support import qapp

from ui.platform.theme import (
    DEFAULT_PALETTE,
    ThemeService,
    apply_theme,
    theme_service,
)


class ThemeServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qapp()

    def test_token_and_custom_palette(self):
        service = ThemeService()
        self.assertEqual(
            service.token("bg_main"),
            DEFAULT_PALETTE.bg_main,
        )
        custom = replace(DEFAULT_PALETTE, bg_main="#101010")
        service.apply(self.app, custom)
        self.assertEqual(service.token("bg_main"), "#101010")
        self.assertIn("#101010", self.app.styleSheet())
        self.assertIs(theme_service(), theme_service())

    def test_listeners(self):
        service = ThemeService()
        seen = []
        service.subscribe(lambda palette: seen.append(palette.bg_main))
        service.apply(self.app)
        self.assertTrue(seen)

    def test_listener_error_is_isolated(self):
        service = ThemeService()
        seen = []

        def broken(_palette):
            raise RuntimeError("theme listener failed")

        service.subscribe(broken)
        service.subscribe(lambda _palette: seen.append(True))
        with self.assertLogs("ui.platform.theme", level="ERROR"):
            service.apply(self.app)
        self.assertTrue(seen)

    def test_apply_theme_wrapper(self):
        palette = apply_theme(self.app)
        self.assertIs(palette, theme_service().palette)
        self.assertEqual(
            theme_service().token("tab_bg"),
            DEFAULT_PALETTE.tab_bg,
        )


if __name__ == "__main__":
    unittest.main()
