# -*- coding: utf-8 -*-
"""Дамп секций и сравнение семантического паритета."""

import json
import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401
from tests.semantic_support import build_service, write_ltx

from core.semantic.parity import (
    compare_section_dumps,
    dump_section_summary,
    load_known_differences,
    parse_manifest,
    parse_section_dump,
)
from core.semantic.query import (
    SectionQueryService,
    missing_include_diagnostics,
)


class SemanticParityTest(unittest.TestCase):
    def _fixture(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        cfg = Path(temp.name) / "configs"
        cfg.mkdir()
        write_ltx(
            cfg / "system.ltx",
            "[alpha]\na = 1\n[beta]:alpha\nb = 2\n",
        )
        forest, service, cache = build_service(cfg)
        self.addCleanup(cache.clear)
        root = SectionQueryService(service).root(
            cfg / "system.ltx",
            forest,
            cfg,
        )
        return cfg, forest, root

    def test_summary_dump_is_sorted_and_reproducible(self):
        _cfg, _forest, root = self._fixture()

        first = dump_section_summary(root.sections)
        second = dump_section_summary(root.sections)

        self.assertEqual(first, second)
        lines = first.splitlines()
        self.assertEqual(lines[0], "section\tkey_count")
        self.assertEqual(
            [line.split("\t")[0] for line in lines[1:]],
            ["alpha", "beta"],
        )
        self.assertEqual(
            parse_section_dump(first),
            {"alpha": 1, "beta": 2},
        )

    def test_property_dump_parses_to_counts(self):
        dump = (
            "section\tkey\tvalue\tfile\tdepth\toperation\tconfidence\n"
            "alpha\ta\t1\tsystem.ltx\t0\tset\texact\n"
            "alpha\tb\t2\tsystem.ltx\t0\tset\texact\n"
            "beta\ta\t1\tsystem.ltx\t0\tset\texact\n"
        )
        self.assertEqual(
            parse_section_dump(dump),
            {"alpha": 2, "beta": 1},
        )

    def test_compare_detects_names_counts_and_known_differences(self):
        result = compare_section_dumps(
            {"alpha": 1, "beta": 2, "gone": 1},
            {"alpha": 1, "beta": 3, "runtime": 0},
            known={"gone"},
        )

        self.assertEqual(result.only_resolver, ())
        self.assertEqual(result.only_game, ("runtime",))
        self.assertEqual(
            result.count_differences,
            (("beta", 2, 3),),
        )
        self.assertEqual(result.known_ignored, ("gone",))
        self.assertFalse(result.ok)
        self.assertIn("only_game\truntime", result.as_text())

        counts_only = compare_section_dumps(
            {"alpha": 1},
            {"alpha": 2},
        )
        self.assertEqual(counts_only.only_resolver, ())
        self.assertEqual(counts_only.only_game, ())
        self.assertEqual(
            counts_only.count_differences,
            (("alpha", 1, 2),),
        )
        self.assertFalse(counts_only.ok)

    def test_known_differences_file_and_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            known_path = Path(d) / "known.json"
            known_path.write_text(
                json.dumps(
                    {
                        "format": "ace-known-parity-differences-v1",
                        "entries": [
                            {"section": "runtime_only", "reason": "test"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            manifest_path = Path(d) / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "sections": {
                            "alpha": 1,
                            "beta": 2,
                        }
                    }
                ),
                encoding="utf-8",
            )

            known = load_known_differences(known_path)
            manifest = parse_manifest(manifest_path)

        self.assertEqual(known, {"runtime_only"})
        self.assertEqual(manifest, {"alpha": 1, "beta": 2})

    def test_missing_include_is_diagnostic_for_root_subtree(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        cfg = Path(temp.name) / "configs"
        cfg.mkdir()
        write_ltx(
            cfg / "system.ltx",
            '[alpha]\n#include "missing.ltx"\n',
        )
        write_ltx(cfg / "other.ltx", '[other]\n#include "nope.ltx"\n')
        forest, _service, cache = build_service(cfg)
        self.addCleanup(cache.clear)

        self.assertTrue(forest.missing_includes)
        diagnostics = missing_include_diagnostics(
            forest,
            cfg / "system.ltx",
        )
        codes = [item.code for item in diagnostics]
        self.assertIn("missing_include", codes)
        self.assertTrue(
            any("missing.ltx" in item.message for item in diagnostics)
        )
        # Чужой корень не должен попадать в диагностику этого корня.
        self.assertFalse(
            any("nope.ltx" in item.message for item in diagnostics)
        )

    def test_nested_missing_include_belongs_to_root(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        cfg = Path(temp.name) / "configs"
        (cfg / "sub").mkdir(parents=True)
        write_ltx(
            cfg / "system.ltx",
            '[alpha]\n#include "sub/a.ltx"\n',
        )
        write_ltx(
            cfg / "sub" / "a.ltx",
            '[nested]\n#include "missing_nested.ltx"\n',
        )
        forest, _service, cache = build_service(cfg)
        self.addCleanup(cache.clear)

        diagnostics = missing_include_diagnostics(
            forest,
            cfg / "system.ltx",
        )
        self.assertTrue(
            any(
                "missing_nested.ltx" in item.message
                for item in diagnostics
            )
        )


if __name__ == "__main__":
    unittest.main()
