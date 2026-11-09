# -*- coding: utf-8 -*-
"""Тесты поиска, сортировки и отчёта по дереву."""

import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401
from tests.helpers import write_text

from core.tools.cache import FileCacheSystem
from core.tree.builder import TreeBuilder
from core.tree.query import TreeQueryService


class TreeQueryServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg = Path(self.tmp.name) / "gamedata" / "configs"
        root = write_text(
            cfg / "system.ltx",
            '[system]\n#include "weapons.ltx"\n',
        )
        write_text(cfg / "weapons.ltx", "[w]\nx = 1\n")
        write_text(cfg / "ui" / "menu.xml", "<menu/>\n")
        self.cfg = cfg
        self.forest = TreeBuilder(FileCacheSystem()).build(
            cfg,
            root,
            use_cache=False,
        )
        self.service = TreeQueryService()

    def test_search_is_case_insensitive_and_substring_based(self):
        self.assertEqual(
            [path.name for path in self.service.search_nodes(
                self.forest, "weap"
            )],
            ["weapons.ltx"],
        )
        self.assertEqual(
            [path.name for path in self.service.search_nodes(
                self.forest, "WEAP"
            )],
            ["weapons.ltx"],
        )
        self.assertEqual(
            self.service.search_nodes(self.forest, "no-such-file"),
            [],
        )

    def test_sort_by_path_and_reverse(self):
        ascending = self.service.sort_nodes(
            self.forest,
            self.forest.files,
            "path",
        )
        descending = self.service.sort_nodes(
            self.forest,
            self.forest.files,
            "path",
            reverse=True,
        )

        self.assertEqual(
            [path.name for path in ascending],
            ["system.ltx", "weapons.ltx"],
        )
        self.assertEqual(descending, list(reversed(ascending)))

    def test_report_contains_tree_metadata(self):
        report = self.service.report(self.forest)

        self.assertIn("Scan dir:", report)
        self.assertIn("System.ltx:", report)
        self.assertIn("Total .ltx files: 2", report)
        self.assertIn("system.ltx", report)
        self.assertIn(str(Path("ui") / "menu.xml"), report)


if __name__ == "__main__":
    unittest.main()
