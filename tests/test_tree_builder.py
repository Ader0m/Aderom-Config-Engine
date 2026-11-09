# -*- coding: utf-8 -*-
"""Тесты построения объективного дерева VFS."""

import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401
from tests.helpers import write_text

from core.tools.cache import FileCacheSystem
from core.tree.builder import TreeBuilder


class TreeBuilderTest(unittest.TestCase):
    def _build(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg = Path(self.tmp.name) / "gamedata" / "configs"
        root = write_text(
            cfg / "system.ltx",
            '[system]\n#include "a.ltx"\n',
        )
        included = write_text(cfg / "a.ltx", "[a]\nx = 1\n")
        separate = write_text(
            cfg / "sub" / "b.ltx",
            "[b]\ny = 2\n",
        )
        xml = write_text(
            cfg / "ui" / "menu.xml",
            "<menu/>\n",
        )
        forest = TreeBuilder(FileCacheSystem()).build(
            cfg,
            root,
            use_cache=False,
        )
        return cfg, root, included, separate, xml, forest

    def test_builds_roots_includes_xml_and_counts(self):
        cfg, root, included, separate, xml, forest = self._build()

        self.assertEqual(
            {path.name for path in forest.roots},
            {"system.ltx", "b.ltx", "menu.xml"},
        )
        self.assertIn(root, forest.roots)
        self.assertIn(separate, forest.roots)
        self.assertNotIn(included, forest.roots)
        self.assertIn(xml, forest.xml_files)
        self.assertEqual(forest.total_files, 3)
        self.assertEqual(forest.total_xml_files, 1)

        self.assertEqual(
            forest.include_parents[included],
            {root},
        )
        self.assertIn(included, forest.include_children[root])
        self.assertEqual(forest.subtree_counts[root], 2)
        self.assertEqual(
            forest.rel_paths[included],
            "a.ltx",
        )
        self.assertEqual(
            forest.rel_paths[separate],
            str(Path("sub") / "b.ltx"),
        )

    def test_display_children_include_included_file(self):
        _cfg, root, included, _separate, _xml, forest = self._build()

        children = forest.display_children[root]
        self.assertIn(included, children)
        self.assertNotIn(root, children)

    def test_cache_returns_same_forest(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "gamedata" / "configs"
            root = write_text(
                cfg / "system.ltx",
                '[system]\n#include "a.ltx"\n',
            )
            write_text(cfg / "a.ltx", "[a]\nx = 1\n")
            builder = TreeBuilder(FileCacheSystem())

            first = builder.build(cfg, root, use_cache=True)
            second = builder.build(cfg, root, use_cache=True)

            self.assertIs(first, second)
            self.assertEqual(second.total_files, 2)


if __name__ == "__main__":
    unittest.main()
