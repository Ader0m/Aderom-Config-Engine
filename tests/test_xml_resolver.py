# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support import SRC  # noqa: F401

from core.models import LineSource
from core.xml_resolver import XmlResolver


class XmlResolverTest(unittest.TestCase):
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
        (self.scripts / "modxml_fix_faction_spot_size.script").write_text(
            "-- patch\n", encoding="utf-8",
        )

        (self.cfg / "ui" / "parts" / "a.xml").write_text(
            '<a x="1"/>\n', encoding="cp1251",
        )
        (self.cfg / "ui" / "main.xml").write_text(
            '<root>\n'
            '#include "ui\\parts\\*.xml"\n'
            '<inner/>\n'
            '</root>\n',
            encoding="cp1251",
        )
        (self.cfg / "ui" / "map_spots.xml").write_text(
            '<map_spots>\n'
            '  <circle_army_spot width="28" height="28"/>\n'
            '</map_spots>\n',
            encoding="cp1251",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_includes_and_line_sources(self):
        resolver = XmlResolver(
            self.cfg,
            scripts_dir=self.scripts,
            dxml=False,
        )
        result = resolver.resolve_output(self.cfg / "ui" / "main.xml")

        self.assertIn("<a x=\"1\"/>", result.text)
        self.assertIn("<inner/>", result.text)
        self.assertEqual(
            len(result.line_sources),
            len(result.text.splitlines()),
        )
        self.assertTrue(
            any(
                isinstance(src, LineSource) and src.kind == "file"
                for src in result.line_sources
            )
        )
        self.assertTrue(
            any(
                isinstance(src, LineSource) and src.kind == "original"
                for src in result.line_sources
            )
        )

    def test_dependency_paths_cached_between_keys(self):
        resolver = XmlResolver(
            self.cfg,
            scripts_dir=self.scripts,
            dxml=False,
        )
        main = self.cfg / "ui" / "main.xml"
        calls = []
        real = resolver.dependency_paths

        def counting(path):
            calls.append(path)
            return real(path)

        with patch.object(resolver, "dependency_paths", counting):
            first = resolver.cache_key(main)
            second = resolver.cache_key(main)

        self.assertEqual(first, second)
        self.assertEqual(calls, [main])

    def test_refresh_recomputes_dependencies(self):
        resolver = XmlResolver(
            self.cfg,
            scripts_dir=self.scripts,
            dxml=False,
        )
        main = self.cfg / "ui" / "main.xml"
        resolver.cache_key(main)
        resolver.refresh()

        calls = []
        real = resolver.dependency_paths

        def counting(path):
            calls.append(path)
            return real(path)

        with patch.object(resolver, "dependency_paths", counting):
            resolver.cache_key(main)

        self.assertEqual(calls, [main])

    def test_dxml_and_pretty_output(self):
        resolver = XmlResolver(
            self.cfg,
            scripts_dir=self.scripts,
        )
        result = resolver.resolve_output(self.cfg / "ui" / "map_spots.xml")

        self.assertIn('width="24"', result.text)
        self.assertIn('height="24"', result.text)
        self.assertGreater(len(result.text.splitlines()), 1)
        self.assertTrue(
            any(
                isinstance(src, LineSource) and src.kind == "patch"
                for src in result.line_sources
            )
        )


if __name__ == "__main__":
    unittest.main()
