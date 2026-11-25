# -*- coding: utf-8 -*-
"""Дамп секций и сравнение семантического паритета."""

import contextlib
import importlib.util
import io
import json
import logging
import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401
from tests.semantic_support import build_service, write_ltx

from core.semantic.parity import (
    PROPERTY_HEADER,
    compare_property_dumps,
    compare_section_dumps,
    dump_section_summary,
    load_known_differences,
    parse_manifest,
    parse_property_dump,
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

        known_counts = compare_section_dumps(
            {"alpha": 1},
            {"alpha": 2},
            known={"alpha"},
        )
        self.assertEqual(known_counts.count_differences, ())
        self.assertEqual(known_counts.known_ignored, ("alpha",))
        self.assertTrue(known_counts.ok)

        known_without_differences = compare_section_dumps(
            {"alpha": 1},
            {"alpha": 1},
            known={"alpha"},
        )
        self.assertEqual(known_without_differences.known_ignored, ())
        self.assertTrue(known_without_differences.ok)

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

    def test_property_dump_parses_values_and_sources(self):
        dump = (
            PROPERTY_HEADER
            + "\n"
            + "Alpha\tA\t1\tsystem.ltx\t0\tset\texact\n"
            + "beta\tb\t\tmods/b.ltx\t2\tcreate\tprobable\n"
        )

        rows = parse_property_dump(dump)

        self.assertEqual(set(rows), {("alpha", "a"), ("beta", "b")})
        row = rows[("alpha", "a")][0]
        self.assertEqual(row.value, "1")
        self.assertEqual(row.file, "system.ltx")
        self.assertEqual(row.depth, "0")
        self.assertEqual(row.operation, "set")
        self.assertEqual(row.confidence, "exact")
        self.assertEqual(rows[("beta", "b")][0].value, "")
        self.assertEqual(
            set(parse_property_dump("\ufeff" + dump)),
            set(rows),
        )

        with tempfile.TemporaryDirectory() as d:
            resolved = parse_property_dump(
                PROPERTY_HEADER
                + "\n"
                + "alpha\ta\t1\tmods/a.ltx\n",
                source_root=d,
            )
            self.assertEqual(
                Path(resolved[("alpha", "a")][0].file),
                Path(d) / "mods" / "a.ltx",
            )

    def test_property_dump_rejects_malformed_rows_and_groups_keys(self):
        with self.assertRaises(ValueError):
            parse_property_dump("alpha\ta\n")

        rows = parse_property_dump(
            PROPERTY_HEADER
            + "\n"
            + "alpha\tSound_Idle\t1\n"
            + "alpha\tsound_idle\t2\n"
        )
        self.assertEqual(len(rows[("alpha", "sound_idle")]), 2)

    def test_property_compare_detects_keys_values_sources_and_known(self):
        resolver = parse_property_dump(
            PROPERTY_HEADER
            + "\n"
            + "alpha\ta\t1\tC:/cfg/a.ltx\t0\tset\texact\n"
            + "alpha\tb\t2\tC:/cfg/a.ltx\t0\tset\texact\n"
            + "alpha\tz\t10\tC:/cfg/a.ltx\t0\tset\texact\n"
            + "alpha\tz\t11\tC:/cfg/a.ltx\t0\tset\texact\n"
            + "beta\tc\t3\tC:/cfg/b.ltx\t1\tappend\texact\n"
            + "gamma\td\t4\tC:/cfg/g.ltx\t1\tset\texact\n"
        )
        game = parse_property_dump(
            PROPERTY_HEADER
            + "\n"
            + "alpha\ta\t1\tC:/cfg/a.ltx\t0\tset\texact\n"
            + "alpha\tb\t9\tC:/cfg/a.ltx\t0\tset\texact\n"
            + "alpha\tx\t7\tC:/cfg/x.ltx\t0\tset\texact\n"
            + "alpha\tz\t10\tC:/cfg/a.ltx\t0\tset\texact\n"
            + "beta\tc\t3\tC:/cfg/other.ltx\t2\tset\texact\n"
            + "delta\te\t5\tC:/cfg/d.ltx\t0\tset\texact\n"
        )

        result = compare_property_dumps(resolver, game, known={"gamma"})

        self.assertEqual(result.only_resolver, ())
        self.assertEqual(result.only_game, ("delta",))
        self.assertEqual(result.only_resolver_keys, ())
        self.assertEqual(result.only_game_keys, (("alpha", "x"),))
        self.assertEqual(
            result.key_count_differences,
            (("alpha", "z", 2, 1),),
        )
        self.assertEqual(
            result.value_differences,
            (("alpha", "b", "2", "9"),),
        )
        self.assertEqual(
            result.source_differences,
            (("beta", "c", "C:/cfg/b.ltx", "C:/cfg/other.ltx"),),
        )
        self.assertEqual(
            result.operation_differences,
            (("beta", "c", "append", "set"),),
        )
        self.assertEqual(
            result.depth_differences,
            (("beta", "c", "1", "2"),),
        )
        self.assertEqual(result.known_ignored, ("gamma",))
        self.assertFalse(result.ok)
        text = result.as_text()
        self.assertIn("only_game\tdelta", text)
        self.assertIn("key_count_difference\talpha\tz\t2\t1", text)
        self.assertIn("value_difference\talpha\tb\t2\t9", text)
        self.assertIn("source_difference\tbeta\tc\t", text)
        self.assertIn("operation_difference\tbeta\tc\tappend\tset", text)
        self.assertIn("depth_difference\tbeta\tc\t1\t2", text)

        accepted = compare_property_dumps(
            resolver,
            game,
            known={"alpha", "gamma"},
        )
        self.assertEqual(accepted.only_game_keys, ())
        self.assertEqual(accepted.key_count_differences, ())
        self.assertEqual(accepted.value_differences, ())
        self.assertEqual(
            accepted.known_ignored,
            ("alpha", "gamma"),
        )

        accepted_all = compare_property_dumps(
            resolver,
            game,
            known={"alpha", "beta", "gamma"},
        )
        self.assertEqual(accepted_all.operation_differences, ())
        self.assertEqual(accepted_all.depth_differences, ())
        self.assertEqual(
            accepted_all.known_ignored,
            ("alpha", "beta", "gamma"),
        )

        identical = compare_property_dumps(resolver, resolver)
        self.assertTrue(identical.ok)
        self.assertEqual(
            identical.as_text().splitlines()[0],
            "resolver_sections\t3",
        )
        accepted_without_differences = compare_property_dumps(
            resolver,
            resolver,
            known={"alpha"},
        )
        self.assertEqual(
            accepted_without_differences.known_ignored,
            (),
        )

    def test_property_compare_row_order_is_optional(self):
        resolver = parse_property_dump(
            PROPERTY_HEADER
            + "\n"
            + "alpha\tk\t1\tC:/cfg/a.ltx\n"
            + "alpha\tk\t2\tC:/cfg/a.ltx\n"
        )
        game = parse_property_dump(
            PROPERTY_HEADER
            + "\n"
            + "alpha\tk\t2\tC:/cfg/a.ltx\n"
            + "alpha\tk\t1\tC:/cfg/a.ltx\n"
        )

        default = compare_property_dumps(resolver, game)
        self.assertTrue(default.ok)
        self.assertEqual(default.row_order_differences, ())

        strict = compare_property_dumps(
            resolver,
            game,
            compare_row_order=True,
        )
        self.assertEqual(
            strict.row_order_differences,
            (("alpha", "k"),),
        )
        self.assertFalse(strict.ok)
        self.assertIn(
            "row_order_difference\talpha\tk",
            strict.as_text(),
        )

        accepted = compare_property_dumps(
            resolver,
            game,
            known={"alpha"},
            compare_row_order=True,
        )
        self.assertEqual(accepted.row_order_differences, ())
        self.assertEqual(accepted.known_ignored, ("alpha",))
        self.assertTrue(accepted.ok)

    def test_property_compare_reports_missing_source(self):
        resolver = parse_property_dump(
            PROPERTY_HEADER
            + "\n"
            + "alpha\ta\t1\tC:/cfg/a.ltx\n"
        )
        game = parse_property_dump(
            PROPERTY_HEADER
            + "\n"
            + "alpha\ta\t1\t\n"
        )

        result = compare_property_dumps(resolver, game)
        self.assertEqual(
            result.source_missing,
            (("alpha", "a", "C:/cfg/a.ltx", ""),),
        )
        self.assertFalse(result.ok)
        self.assertIn("source_missing\talpha\ta\t", result.as_text())

        accepted = compare_property_dumps(resolver, game, known={"alpha"})
        self.assertEqual(accepted.source_missing, ())
        self.assertEqual(accepted.known_ignored, ("alpha",))
        self.assertTrue(accepted.ok)

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


class SemanticParityCliTest(unittest.TestCase):
    @staticmethod
    def _load_cli():
        path = (
            Path(__file__).resolve().parents[1]
            / "tools"
            / "semantic_parity.py"
        )
        spec = importlib.util.spec_from_file_location(
            "ace_semantic_parity_cli",
            path,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg = Path(self.temp.name) / "configs"
        self.cfg.mkdir()
        write_ltx(
            self.cfg / "system.ltx",
            "[alpha]\na = 1\n[beta]:alpha\nb = 2\n",
        )
        self.cli = self._load_cli()

    def _run(self, *args):
        output = io.StringIO()
        errors = io.StringIO()
        previous_disable = logging.root.manager.disable
        try:
            with contextlib.redirect_stdout(output), \
                    contextlib.redirect_stderr(errors):
                code = self.cli.main([str(arg) for arg in args])
        finally:
            logging.disable(previous_disable)
        return code, output.getvalue()

    def _known(self, *sections):
        path = Path(self.temp.name) / "known.json"
        path.write_text(
            json.dumps(
                {
                    "format": "ace-known-parity-differences-v1",
                    "entries": [
                        {"section": name, "reason": "test"}
                        for name in sections
                    ],
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_brief_compare_suppresses_known_count_difference(self):
        game = Path(self.temp.name) / "game_sections.txt"
        game.write_text("alpha\t2\nbeta\t2\n", encoding="utf-8")

        code, _output = self._run(
            "--configs", self.cfg,
            "--compare", game,
        )
        self.assertEqual(code, 1)

        code, output = self._run(
            "--configs", self.cfg,
            "--compare", game,
            "--known", self._known("alpha"),
        )
        self.assertEqual(code, 0)
        self.assertIn("known_ignored\talpha", output)

    def test_property_compare_cli_and_bom(self):
        properties = Path(self.temp.name) / "game_properties.txt"
        code, _output = self._run(
            "--configs", self.cfg,
            "--properties", properties,
            "--quiet",
        )
        self.assertEqual(code, 0)

        code, _output = self._run(
            "--configs", self.cfg,
            "--compare-properties", properties,
            "--quiet",
        )
        self.assertEqual(code, 0)

        relative = Path(self.temp.name) / "relative_properties.txt"
        relative.write_text(
            properties.read_text(encoding="utf-8-sig").replace(
                str(self.cfg / "system.ltx"),
                "system.ltx",
            ),
            encoding="utf-8",
        )
        code, _output = self._run(
            "--configs", self.cfg,
            "--compare-properties", relative,
            "--quiet",
        )
        self.assertEqual(code, 1)
        code, _output = self._run(
            "--configs", self.cfg,
            "--compare-properties", relative,
            "--source-root", self.cfg,
            "--quiet",
        )
        self.assertEqual(code, 0)

        changed = Path(self.temp.name) / "changed_properties.txt"
        changed.write_text(
            properties.read_text(encoding="utf-8-sig").replace(
                "alpha\ta\t1\t",
                "alpha\ta\t9\t",
            ),
            encoding="utf-8-sig",
        )
        code, _output = self._run(
            "--configs", self.cfg,
            "--compare-properties", changed,
            "--quiet",
        )
        self.assertEqual(code, 1)

        code, output = self._run(
            "--configs", self.cfg,
            "--compare-properties", changed,
            "--known", self._known("alpha"),
            "--quiet",
        )
        self.assertEqual(code, 0)
        self.assertIn("known_ignored\talpha", output)


    def test_combined_report_and_stale_known(self):
        game = Path(self.temp.name) / "game_sections.txt"
        game.write_text("alpha\t2\nbeta\t2\n", encoding="utf-8")
        properties = Path(self.temp.name) / "game_properties.txt"
        code, _output = self._run(
            "--configs", self.cfg,
            "--properties", properties,
            "--quiet",
        )
        self.assertEqual(code, 0)
        changed = Path(self.temp.name) / "changed_properties.txt"
        changed.write_text(
            properties.read_text(encoding="utf-8-sig").replace(
                "alpha\ta\t1\t",
                "alpha\ta\t9\t",
            ),
            encoding="utf-8",
        )
        report = Path(self.temp.name) / "report.json"

        code, output = self._run(
            "--configs", self.cfg,
            "--compare", game,
            "--compare-properties", changed,
            "--known", self._known("alpha"),
            "--report", report,
            "--release-gate",
            "--quiet",
        )
        self.assertEqual(code, 0)
        self.assertIn("[summary]", output)
        self.assertIn("[properties]", output)
        payload = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(payload["format"], "ace-parity-report-v1")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["modes"], ["properties", "summary"])
        self.assertTrue(payload["complete"])
        self.assertEqual(payload["missing_modes"], [])
        self.assertTrue(payload["release_gate_ready"])
        self.assertEqual(payload["known"]["ignored"], ["alpha"])
        self.assertEqual(payload["known"]["unused"], [])
        self.assertTrue(payload["results"]["summary"]["ok"])
        self.assertTrue(payload["results"]["properties"]["ok"])
        self.assertIn("sha256", payload["game"]["sections"])
        self.assertIn("sha256", payload["game"]["properties"])

        clean_game = Path(self.temp.name) / "clean_sections.txt"
        clean_game.write_text("alpha\t1\nbeta\t2\n", encoding="utf-8")
        clean_report = Path(self.temp.name) / "clean_report.json"
        code, _output = self._run(
            "--configs", self.cfg,
            "--compare", clean_game,
            "--compare-properties", properties,
            "--known", self._known("alpha"),
            "--report", clean_report,
            "--release-gate",
            "--quiet",
        )
        self.assertEqual(code, 1)
        payload = json.loads(clean_report.read_text(encoding="utf-8"))
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["complete"])
        self.assertEqual(payload["missing_modes"], [])
        self.assertFalse(payload["release_gate_ready"])
        self.assertEqual(payload["known"]["unused"], ["alpha"])
        self.assertTrue(payload["results"]["summary"]["ok"])
        self.assertTrue(payload["results"]["properties"]["ok"])


    def test_partial_report_is_not_release_gate_ready(self):
        game = Path(self.temp.name) / "sections.txt"
        game.write_text("alpha\t1\nbeta\t2\n", encoding="utf-8")
        report = Path(self.temp.name) / "partial.json"

        code, _output = self._run(
            "--configs", self.cfg,
            "--compare", game,
            "--report", report,
            "--quiet",
        )
        self.assertEqual(code, 0)
        payload = json.loads(report.read_text(encoding="utf-8"))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["modes"], ["summary"])
        self.assertFalse(payload["complete"])
        self.assertEqual(payload["missing_modes"], ["properties"])
        self.assertFalse(payload["release_gate_ready"])

        with self.assertRaises(SystemExit):
            self._run(
                "--configs", self.cfg,
                "--compare", game,
                "--report", report,
                "--release-gate",
                "--quiet",
            )


if __name__ == "__main__":
    unittest.main()
