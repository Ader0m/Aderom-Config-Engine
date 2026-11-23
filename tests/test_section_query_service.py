# -*- coding: utf-8 -*-
"""SectionQueryService: страницы, поиск, дамп и кэш по снимку."""

import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401
from tests.semantic_support import build_service, write_ltx

from core.semantic.query import SectionQueryService


class SectionQueryServiceTest(unittest.TestCase):
    def _fixture(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        config_dir = Path(temp.name) / "configs"
        config_dir.mkdir(parents=True)
        write_ltx(
            config_dir / "system.ltx",
            "[base]\n"
            "a = 1\n"
            "b = 2\n"
            "[child]:base\n"
            "c = 3\n"
            "[empty]\n",
        )
        write_ltx(
            config_dir / "mod_system_01.ltx",
            "![base]\na = 10\n",
        )
        forest, service, cache = build_service(config_dir)
        self.addCleanup(cache.clear)
        return config_dir, forest, service, cache

    def test_root_is_cached_by_snapshot_and_invalidated(self):
        cfg, forest, service, cache = self._fixture()
        query = SectionQueryService(service)

        first = query.root(cfg / "system.ltx", forest, cfg)
        second = query.root(cfg / "system.ltx", forest, cfg)
        self.assertIs(first, second)

        cache.invalidate_snapshot(forest.cache_id)
        third = query.root(cfg / "system.ltx", forest, cfg)
        self.assertIsNot(first, third)

    def test_section_and_find_pagination(self):
        cfg, forest, service, _cache = self._fixture()
        query = SectionQueryService(service)

        section = query.section(
            cfg / "system.ltx",
            "BASE",
            forest,
            cfg,
        )
        self.assertEqual(section.name, "base")

        empty = query.section(
            cfg / "system.ltx",
            "empty",
            forest,
            cfg,
        )
        self.assertEqual(empty.kind, "base")
        self.assertEqual(empty.properties, ())

        found = query.find_sections(
            cfg / "system.ltx",
            "",
            forest,
            cfg,
            offset=0,
            limit=2,
        )
        self.assertEqual(found.total, 3)
        self.assertEqual(len(found.names), 2)

        exact = query.find_sections(
            cfg / "system.ltx",
            "child",
            forest,
            cfg,
            exact=True,
        )
        self.assertEqual(exact.names, ("child",))

    def test_properties_filter_offset_and_limit(self):
        cfg, forest, service, _cache = self._fixture()
        query = SectionQueryService(service)

        page = query.properties(
            cfg / "system.ltx",
            "base",
            forest,
            cfg,
            offset=1,
            limit=1,
        )
        self.assertEqual(page.total, 2)
        self.assertEqual([item.key for item in page.properties], ["b"])

        filtered = query.properties(
            cfg / "system.ltx",
            "base",
            forest,
            cfg,
            keys=("a",),
        )
        self.assertEqual(filtered.total, 1)
        self.assertEqual(filtered.properties[0].value, "10")

    def test_unknown_section_and_dump(self):
        cfg, forest, service, _cache = self._fixture()
        query = SectionQueryService(service)

        missing = query.properties(
            cfg / "system.ltx",
            "no_such",
            forest,
            cfg,
        )
        self.assertIsNone(missing.section)
        self.assertIn(
            "unknown_section",
            {item.code for item in missing.diagnostics},
        )

        dump = query.dump(
            cfg / "system.ltx",
            forest,
            cfg,
            section="base",
        )
        self.assertTrue(dump.startswith("section\tkey"))
        self.assertIn("base\ta\t10", dump)
        self.assertEqual(
            dump,
            query.dump(
                cfg / "system.ltx",
                forest,
                cfg,
                section="base",
            ),
        )

        lines = list(
            query.iter_dump(
                cfg / "system.ltx",
                forest,
                cfg,
                section="base",
            )
        )
        self.assertTrue(lines[0].startswith("section\tkey"))
        self.assertGreater(len(lines), 1)

        with self.assertRaises(KeyError):
            query.dump(
                cfg / "system.ltx",
                forest,
                cfg,
                section="no_such",
            )


if __name__ == "__main__":
    unittest.main()
