# -*- coding: utf-8 -*-
"""Сохранение правки названия/описания в режимах XML-патча проекта."""

import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from app.projects import ProjectService
from core.items.localization import edit_localization_text
from tests.test_project_xml_patches import ForkServices, Forest, Services


BEFORE = """<?xml version="1.0" encoding="windows-1251"?>
<string_table>
    <string id="st_name"><text>Старое имя</text></string>
    <string id="st_desc"><text>Старое описание</text></string>
</string_table>
"""


class LocalizationXmlModesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config_dir = Path(self.temp.name) / "configs"
        self.source = self.config_dir / "text" / "rus" / "ru.xml"
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(BEFORE.encode("windows-1251"))
        self.forest = Forest(self.config_dir, [self.source])
        self.project = ProjectService(
            Path(self.temp.name) / "projects"
        )
        self.project.create("proj")
        self.after = edit_localization_text(
            BEFORE,
            "st_name",
            "Новое имя",
        )

    def _preview(self, services):
        self.project.update_draft(self.source, self.after)
        return self.project.build_preview(services, self.forest)

    def test_override_saves_full_xml_with_edited_text(self):
        self.project.set_xml_patch_mode("xml_override")
        preview = self._preview(self.services())

        item = preview.items[0]
        self.assertTrue(item.ok, item.error)
        self.assertEqual(item.plan.xml_mode, "full_copy")
        self.assertEqual(item.generated.files[0].text, self.after)
        self.assertIn("<text>Новое имя</text>", self.after)

    def test_xml_mode_forks_key_and_keeps_edited_text(self):
        self.project.set_xml_patch_mode("xml")
        preview = self._preview(ForkServices())

        item = preview.items[0]
        self.assertTrue(item.ok, item.error)
        self.assertEqual(item.plan.format, "xml_fork")
        names = [
            Path(file.relative_path).name
            for file in item.generated.files
        ]
        self.assertTrue(any(name.endswith("_ace_proj.xml") for name in names))
        addition = next(
            file for file in item.generated.files
            if Path(file.relative_path).name.endswith("_ace_proj.xml")
        )
        self.assertIn("<text>Новое имя</text>", addition.text)

    def test_dxml_lua_mode_is_planned_for_edited_text(self):
        self.project.set_patch_mode("on_file")
        self.project.set_xml_patch_mode("dxml_lua")
        preview = self._preview(self.services())

        item = preview.items[0]
        self.assertTrue(item.ok, item.error)
        self.assertEqual(item.plan.xml_mode, "dxml_lua")

    def services(self):
        return Services()


if __name__ == "__main__":
    unittest.main()
