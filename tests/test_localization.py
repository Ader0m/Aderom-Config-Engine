# -*- coding: utf-8 -*-
"""Тесты строковых таблиц игры (configs/text/<lang>/*.xml)."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.helpers import write_text, touch_after
from tests.support import SRC  # noqa: F401

from core.items.localization import LocalizationService, clean_text


ENGLISH = """<?xml version="1.0" encoding="windows-1251"?>
<string_table>
    <string id="st_shared"><text>English name</text></string>
    <string id="st_only_eng"><text>Only English</text></string>
</string_table>
"""

RUSSIAN = """<?xml version="1.0" encoding="windows-1251"?>
<string_table>
    <string id="st_shared"><text>Русское имя</text></string>
    <string id="st_rich"><text>Строка\\n%c[ui_gray_3]Характеристики:\\n&lt;b&gt;тест&lt;/b&gt;</text></string>
</string_table>
"""


class LocalizationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.configs = self.root / "gamedata" / "configs"
        (self.configs / "text" / "eng").mkdir(parents=True)
        (self.configs / "text" / "rus").mkdir(parents=True)
        write_text(
            self.configs / "localization.ltx",
            "[string_table]\nlanguage = eng\n",
        )
        write_text(self.configs / "text" / "eng" / "en.xml", ENGLISH)
        write_text(self.configs / "text" / "rus" / "ru.xml", RUSSIAN)

    def tearDown(self):
        self._tmp.cleanup()

    def test_russian_overrides_english_with_fallback(self):
        table = LocalizationService().load(config_dir=self.configs)

        self.assertEqual(table.language, "rus")
        self.assertEqual(table.configured_language, "eng")
        self.assertEqual(table.translate("st_shared"), "Русское имя")
        self.assertEqual(table.translate("st_only_eng"), "Only English")
        self.assertEqual(table.translate("st_missing"), "st_missing")

    def test_text_is_cleaned_for_qt(self):
        table = LocalizationService().load(config_dir=self.configs)
        text = table.translate("st_rich")

        self.assertIn("Строка\n", text)
        self.assertNotIn("%c[", text)
        self.assertNotIn("<b>", text)
        self.assertIn("Характеристики:", text)

    def test_entry_keeps_raw_text_for_editor(self):
        table = LocalizationService().load(config_dir=self.configs)
        entry = table.entry("st_rich")

        self.assertIsNotNone(entry)
        self.assertIn(r"Строка\n%c[ui_gray_3]", entry.raw_text)
        self.assertIn("<b>тест</b>", entry.raw_text)
        self.assertNotIn("&lt;", entry.raw_text)

    def test_include_is_expanded(self):
        write_text(
            self.configs / "text" / "rus" / "extra.xml",
            '<string_table>\n#include "part.xml"\n</string_table>\n',
        )
        write_text(
            self.configs / "text" / "rus" / "part.xml",
            '<string id="st_part"><text>Часть</text></string>\n',
        )

        table = LocalizationService().load(config_dir=self.configs)
        self.assertEqual(table.translate("st_part"), "Часть")

    def test_signature_changes_when_text_file_changes(self):
        service = LocalizationService()
        first = service.signature(config_dir=self.configs)

        path = self.configs / "text" / "rus" / "ru.xml"
        touch_after(path)

        second = service.signature(config_dir=self.configs)
        self.assertNotEqual(first, second)

    def test_clean_text_handles_escapes_and_colors(self):
        self.assertEqual(
            clean_text("A\\n%c[d_blue]B"),
            "A\nB",
        )

    def test_real_xml_newlines_are_ignored(self):
        self.assertEqual(
            clean_text("A\\n\n\t\tB"),
            "A\nB",
        )

    def test_br_is_still_a_line_break(self):
        self.assertEqual(clean_text("A<br>B"), "A\nB")


if __name__ == "__main__":
    unittest.main()
