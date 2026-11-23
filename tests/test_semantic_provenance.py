# -*- coding: utf-8 -*-
"""Провенанс значений из Resolver4 без изменения его семантики."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.support import SRC  # noqa: F401
from tests.semantic_support import build_service, write_ltx


class SemanticProvenanceTest(unittest.TestCase):
    def _fixture(self, files):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        config_dir = Path(temp.name) / "configs"
        config_dir.mkdir(parents=True)

        for name, text in files.items():
            write_ltx(config_dir / name, text)

        forest, service, cache = build_service(config_dir)
        self.addCleanup(cache.clear)
        return config_dir, forest, service

    def _standard(self):
        return self._fixture(
            {
                "system.ltx": (
                    "[base]\n"
                    "a = 1\n"
                    "b = 2\n"
                    "list = x,y\n"
                    "[child]:base\n"
                    "c = 3\n"
                    "[removed]:base\n"
                    "d = 4\n"
                    '#include "extra.ltx"\n'
                ),
                "extra.ltx": "[extra]\ne = 5\n",
                "mod_system_01.ltx": (
                    "![base]\n"
                    "a = 10\n"
                    "!b\n"
                    ">list = z\n"
                    "@[created]\n"
                    "x = 1\n"
                    "!![removed]\n"
                ),
            }
        )

    def test_text_and_line_sources_match_existing_resolved_path(self):
        cfg, forest, service = self._standard()

        root = service.assemble_semantic(
            cfg / "system.ltx",
            forest,
            cfg,
            snapshot_id="snap-1",
        )
        text, sources = service.assemble_resolved(
            cfg / "system.ltx",
            forest,
            cfg,
        )

        self.assertEqual(root.snapshot_id, "snap-1")
        self.assertEqual(root.root.path, cfg / "system.ltx")
        self.assertEqual(root.text, text)
        self.assertIs(root.text, text)
        self.assertEqual(list(root.line_sources), sources)
        self.assertEqual(len(root.text.split("\n")), len(root.line_sources))

    def test_semantic_reuses_text_already_cached_by_editor(self):
        cfg, forest, service = self._standard()

        text, _sources = service.assemble_resolved(
            cfg / "system.ltx",
            forest,
            cfg,
        )
        root = service.assemble_semantic(
            cfg / "system.ltx",
            forest,
            cfg,
        )

        self.assertIs(root.text, text)
        self.assertEqual(root.sections.get("base").kind, "override")

    def test_projection_matches_independent_golden(self):
        cfg, forest, service = self._fixture(
            {
                "system.ltx": (
                    "[alpha]\n"
                    "a = 1\n"
                    "[beta]:alpha\n"
                    "b = 2\n"
                    '#include "extra.ltx"\n'
                ),
                "extra.ltx": "[gamma]\nc = 3\n",
            }
        )

        root = service.assemble_semantic(
            cfg / "system.ltx",
            forest,
            cfg,
            snapshot_id="snap",
        )
        adapter = service.get_adapter()
        expected_text = "\n".join(
            [
                "; Assembled root: %s" % (cfg / "system.ltx"),
                "; Resolver module: %s" % adapter.resolver_script,
                "",
                "[alpha]",
                "a = 1",
                "",
                "[beta]",
                "a = 1",
                "b = 2",
                "",
                "[gamma]",
                "c = 3",
                "",
            ]
        )
        original = ("original", cfg / "system.ltx")
        extra = ("file", cfg / "extra.ltx")
        expected_sources = [
            None,
            None,
            None,
            original,
            original,
            None,
            original,
            original,
            original,
            None,
            extra,
            extra,
            None,
        ]

        self.assertEqual(root.text, expected_text)
        self.assertEqual(
            [
                None if source is None else (source.kind, source.path)
                for source in root.line_sources
            ],
            expected_sources,
        )

    def test_model_winners_match_line_sources(self):
        cfg, forest, service = self._standard()
        root = service.assemble_semantic(
            cfg / "system.ltx",
            forest,
            cfg,
            snapshot_id="snap",
        )
        lines = root.text.split("\n")

        for section_name in ("base", "child", "created", "extra"):
            section = root.sections.get(section_name)
            start = lines.index("[%s]" % section_name)
            end = len(lines)
            for index in range(start + 1, len(lines)):
                if lines[index].startswith("["):
                    end = index
                    break

            for prop in section.properties:
                if prop.value is None or "\n" in str(prop.value):
                    continue
                prefix = "%s =" % prop.key
                line_index = None
                for index in range(start + 1, end):
                    if lines[index].startswith(prefix):
                        line_index = index
                        break
                self.assertIsNotNone(
                    line_index,
                    "нет строки для %s/%s" % (section_name, prop.key),
                )
                source = root.line_sources[line_index]
                self.assertIsNotNone(source)
                self.assertEqual(Path(source.path), prop.winner.file)

    def test_override_inheritance_create_and_delete(self):
        cfg, forest, service = self._standard()
        root = service.assemble_semantic(
            cfg / "system.ltx",
            forest,
            cfg,
            snapshot_id="snap",
        )

        self.assertEqual(
            root.sections.names,
            ("base", "child", "created", "extra"),
        )
        self.assertIsNone(root.sections.get("removed"))

        base = root.sections.get("base")
        self.assertEqual(base.kind, "override")
        self.assertEqual(base.declaration.file.name, "system.ltx")
        self.assertEqual(base.source.file.name, "mod_system_01.ltx")
        props = {item.key: item for item in base.properties}

        self.assertEqual(props["a"].value, "10")
        self.assertEqual(props["a"].operation, "set")
        self.assertEqual(props["a"].winner.file.name, "mod_system_01.ltx")
        self.assertEqual(props["a"].winner.depth, -200)
        self.assertEqual(props["a"].winner.layer, "raw")
        self.assertEqual(
            [source.file.name for source in props["a"].chain],
            ["system.ltx", "mod_system_01.ltx"],
        )
        self.assertNotIn("b", props)

        self.assertEqual(props["list"].value, "x,y,z")
        self.assertEqual(props["list"].operation, "append")
        self.assertEqual(props["list"].confidence, "probable")
        self.assertEqual(
            props["list"].chain.operations,
            ("set", "append"),
        )

        child = root.sections.get("child")
        child_props = {item.key: item for item in child.properties}
        self.assertEqual(child_props["a"].value, "10")
        self.assertEqual(child_props["a"].winner.section, "base")
        self.assertEqual(child_props["c"].value, "3")

        created = root.sections.get("created")
        self.assertEqual(created.kind, "create")
        self.assertEqual(created.properties[0].key, "x")
        self.assertEqual(created.properties[0].operation, "create")

        extra = root.sections.get("extra")
        self.assertEqual(extra.properties[0].key, "e")
        self.assertEqual(extra.properties[0].value, "5")

        codes = {item.code for item in root.diagnostics}
        self.assertIn("section_created", codes)
        self.assertIn("section_deleted", codes)

    def test_missing_parent_and_missing_override_are_diagnostics(self):
        cfg, forest, service = self._fixture(
            {
                "system.ltx": (
                    "[base]\n"
                    "a = 1\n"
                    "[orphan]:missing\n"
                    "b = 2\n"
                ),
                "mod_system_01.ltx": "![no_such]\nx = 1\n",
            }
        )
        root = service.assemble_semantic(
            cfg / "system.ltx",
            forest,
            cfg,
            snapshot_id="snap",
        )

        codes = {item.code for item in root.diagnostics}
        self.assertIn("missing_parent", codes)
        self.assertIn("override_missing_section", codes)
        self.assertIsNone(root.sections.get("no_such"))

        orphan = root.sections.get("orphan")
        self.assertEqual(orphan.parents, ("missing",))
        self.assertEqual(orphan.properties[0].key, "b")

    def test_resolved_overlay_marks_patch_winner(self):
        original = "[base]\na = 1\nb = 2\n"
        edited = "[base]\na = 42\nb = 2\n"
        cfg, forest, service = self._fixture(
            {"system.ltx": original}
        )
        overlay = SimpleNamespace(
            source_files={},
            resolved_edits={
                cfg / "system.ltx": (original, edited, ()),
            },
        )

        root = service.assemble_semantic(
            cfg / "system.ltx",
            forest,
            cfg,
            overlay=overlay,
            snapshot_id="snap",
        )
        props = {
            item.key: item
            for item in root.sections.get("base").properties
        }

        self.assertEqual(props["a"].value, "42")
        self.assertEqual(props["a"].winner.layer, "patch")
        self.assertEqual(props["a"].confidence, "probable")
        self.assertEqual(
            [source.layer for source in props["a"].chain],
            ["raw", "patch"],
        )
        self.assertEqual(props["b"].value, "2")
        self.assertEqual(props["b"].confidence, "exact")

    def test_overlay_source_file_is_reflected_in_provenance(self):
        cfg, forest, service = self._standard()
        overlay = SimpleNamespace(
            source_files={
                cfg / "extra.ltx": "[extra]\ne = 99\n",
            },
            resolved_edits={},
        )

        root = service.assemble_semantic(
            cfg / "system.ltx",
            forest,
            cfg,
            overlay=overlay,
            snapshot_id="snap",
        )
        extra = root.sections.get("extra")

        self.assertEqual(extra.properties[0].value, "99")
        self.assertEqual(extra.properties[0].winner.file, cfg / "extra.ltx")

if __name__ == "__main__":
    unittest.main()
