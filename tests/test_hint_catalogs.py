# -*- coding: utf-8 -*-
"""Каталоги данных подсказок."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.helpers import FakeForest, write_text
from tests.support import SRC  # noqa: F401

from core.catalogs.colors import ColorCatalog, parse_color_definitions
from core.catalogs.includes import IncludeCatalog
from core.catalogs.localization import LocalizationIndex
from core.catalogs.sections import SectionCatalog
from sdk.api import ProjectOverlay
from sdk.engine_text import parse_color_definitions as sdk_colors


COLOR_XML = """<?xml version="1.0" encoding="windows-1251"?>
<color_defs>
  <color name="ui_gray_3" r="170" g="170" b="170" a="255" />
  <color name="D_Blue" r="80" g="120" b="220" />
</color_defs>
"""


class ColorCatalogTest(unittest.TestCase):
    def test_parse_and_overlay(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "ui").mkdir()
            path = root / "ui" / "color_defs.xml"
            path.write_text(COLOR_XML, encoding="windows-1251")
            forest = FakeForest(root, [])
            catalog = ColorCatalog()
            value = catalog.load(forest)
            self.assertEqual(value.by_name["ui_gray_3"].rgba, (170, 170, 170, 255))
            self.assertEqual(value.by_name["d_blue"].rgba, (80, 120, 220, 255))
            self.assertEqual(
                {name: item.rgba for name, item in value.by_name.items()},
                sdk_colors(COLOR_XML),
            )

            overlay = ProjectOverlay(
                source_files={
                    path: COLOR_XML.replace('name="D_Blue"', 'name="overlay"')
                }
            )
            changed = catalog.load(forest, overlay=overlay)
            self.assertIn("overlay", changed.by_name)
            self.assertNotIn("d_blue", changed.by_name)

    def test_core_parser_matches_sdk(self):
        self.assertEqual(
            parse_color_definitions(COLOR_XML),
            sdk_colors(COLOR_XML),
        )


class SectionCatalogTest(unittest.TestCase):
    def test_sections_keys_parents_and_prefix(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            write_text(
                root / "a.ltx",
                "\n".join(
                    [
                        "[stalker]",
                        "inv_name = st_name",
                        "cost = 10",
                        "[child]:stalker, !old",
                        "key = value",
                        "!![deleted]",
                        "x = 1",
                    ]
                ),
            )
            forest = FakeForest(root, [root / "a.ltx"])
            value = SectionCatalog().build(forest)
            self.assertIn("stalker", value.names)
            self.assertIn("child", value.names)
            self.assertIn("deleted", value.names)
            self.assertEqual(
                value.find("child").parents,
                ("stalker", "old"),
            )
            self.assertIn("inv_name", value.keys_of("stalker"))
            self.assertEqual(
                [item.name for item in value.prefix("sta", limit=10)],
                ["stalker"],
            )
            self.assertTrue(value.find("deleted").is_deleted)


class IncludeCatalogTest(unittest.TestCase):
    def test_paths_and_prefix(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            write_text(root / "configs" / "a.ltx", "[sec]")
            write_text(root / "configs" / "b.xml", "<root/>")
            forest = FakeForest(
                root,
                [root / "configs" / "a.ltx"],
                xml_files=[root / "configs" / "b.xml"],
            )
            value = IncludeCatalog().load(forest)
            paths = [entry.rel_path for entry in value.entries]
            self.assertIn("configs/", paths)
            self.assertIn("configs/a.ltx", paths)
            self.assertIn("configs/b.xml", paths)
            filtered = value.prefix("configs/", limit=10, suffix=".ltx")
            self.assertEqual(
                [entry.rel_path for entry in filtered],
                ["configs/", "configs/a.ltx"],
            )


class LocalizationIndexTest(unittest.TestCase):
    def test_prefix_and_fallback(self):
        class Table:
            strings = {
                "st_item_name": "Имя",
                "st_item_desc": "Описание",
            }
            entries = {}
            signature = "sig"

        value = LocalizationIndex().build(Table())
        self.assertEqual(
            value.prefix("st_item", limit=10),
            ("st_item_desc", "st_item_name"),
        )
        self.assertEqual(
            value.prefix("ST_ITEM", limit=10),
            ("st_item_desc", "st_item_name"),
        )
        self.assertEqual(value.text("st_item_name"), "Имя")


if __name__ == "__main__":
    unittest.main()
