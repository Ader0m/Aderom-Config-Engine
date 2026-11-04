# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.resolved_cache import ResolvedCache
from core.xml_resolve_service import XmlResolveService


class XmlResolveServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = self.root / "gamedata" / "configs"
        self.scripts = self.root / "gamedata" / "scripts"
        (self.cfg / "ui" / "parts").mkdir(parents=True)
        self.scripts.mkdir(parents=True)

        (self.scripts / "dxml_core.script").write_text(
            "-- core\n", encoding="utf-8",
        )
        (self.cfg / "system.ltx").write_text(
            "[system]\n", encoding="cp1251",
        )
        (self.cfg / "ui" / "parts" / "a.xml").write_text(
            '<a x="1"/>\n', encoding="cp1251",
        )
        self.main = self.cfg / "ui" / "main.xml"
        self.main.write_text(
            '<root>\n'
            '#include "ui\\parts\\*.xml"\n'
            '</root>\n',
            encoding="cp1251",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_cache_hit_uses_same_result(self):
        service = XmlResolveService(
            resolved_cache=ResolvedCache(),
            scripts_dir=self.scripts,
        )

        first = service.resolve_output(self.main, self.cfg)
        second = service.resolve_output(self.main, self.cfg)

        self.assertEqual(first, second)
        self.assertIn("<a x=\"1\"/>", first.text)
        self.assertEqual(service.cache_stats()["misses"], 1)
        self.assertEqual(service.cache_stats()["hits"], 1)

    def test_config_dir_forms_share_cache(self):
        service = XmlResolveService(
            resolved_cache=ResolvedCache(),
            scripts_dir=self.scripts,
        )
        game_root = self.cfg.parent.parent

        first = service.resolve_output(self.main, self.cfg)
        second = service.resolve_output(self.main, game_root)

        self.assertEqual(first, second)
        self.assertEqual(service.cache_stats()["misses"], 1)

    def test_include_edit_invalidates_cache(self):
        service = XmlResolveService(
            resolved_cache=ResolvedCache(),
            scripts_dir=self.scripts,
        )
        first = service.resolve_output(self.main, self.cfg)
        self.assertIn("<a x=\"1\"/>", first.text)

        include = self.cfg / "ui" / "parts" / "a.xml"
        include.write_text('<a x="2"/>\n', encoding="cp1251")
        stat = include.stat()
        os.utime(
            include,
            ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000),
        )

        second = service.resolve_output(self.main, self.cfg)
        self.assertIn("<a x=\"2\"/>", second.text)

    def test_scripts_edit_invalidates_cache(self):
        service = XmlResolveService(
            resolved_cache=ResolvedCache(),
            scripts_dir=self.scripts,
        )
        service.resolve_output(self.main, self.cfg)
        service.refresh()

        core = self.scripts / "dxml_core.script"
        core.write_text("-- core v2\n", encoding="utf-8")
        stat = core.stat()
        os.utime(
            core,
            ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000),
        )

        service.resolve_output(self.main, self.cfg)
        self.assertEqual(service.cache_stats()["misses"], 2)

    def test_disk_cache_survives_new_service(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            cache = ResolvedCache(cache_dir=Path(cache_dir))
            first = XmlResolveService(
                resolved_cache=cache,
                scripts_dir=self.scripts,
            )
            first.resolve_output(self.main, self.cfg)

            second_cache = ResolvedCache(cache_dir=Path(cache_dir))
            second = XmlResolveService(
                resolved_cache=second_cache,
                scripts_dir=self.scripts,
            )
            result = second.resolve_output(self.main, self.cfg)

            self.assertIn("<a x=\"1\"/>", result.text)
            self.assertEqual(second.cache_stats()["disk_hits"], 1)


if __name__ == "__main__":
    unittest.main()
