# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.support import SRC  # noqa: F401

from core.dltx_generator import DltxGenerator
from core.multiline_edit import analyze_multiline_edit
from core.patch_models import PatchOptions
from core.patch_planner import PatchPlanner
from core.patch_verifier import ModelPatchVerifier


class _Forest:
    def __init__(self, scan_dir, files, parents=None):
        self.scan_dir = str(scan_dir)
        self.files = [Path(p) for p in files]
        self.include_parents = {
            Path(k): {Path(v) for v in values}
            for k, values in (parents or {}).items()
        }


class MultiLineEditTest(unittest.TestCase):
    def test_multiple_key_changes(self):
        analysis = analyze_multiline_edit(
            "[a]\nx = 1\ny = 2\n",
            "[a]\nx = 3\ny = 4\n",
        )
        self.assertTrue(analysis.is_supported)
        kinds = {(change.kind, change.key) for change in analysis.changes}
        self.assertEqual(
            kinds,
            {("set_key", "x"), ("set_key", "y")},
        )
        self.assertTrue(any("Изменено строк" in note for note in analysis.notes))

    def test_multiline_notes_include_winners(self):
        source = SimpleNamespace(display="MO2\\Weapons\\a.ltx", path=None)
        analysis = analyze_multiline_edit(
            "[a]\nx = 1\ny = 2\n",
            "[a]\nx = 3\ny = 4\n",
            [None, source, source],
        )
        self.assertTrue(
            any("победитель" in note for note in analysis.notes)
        )

    def test_insert_and_delete_key(self):
        added = analyze_multiline_edit(
            "[a]\nx = 1\n",
            "[a]\nx = 1\ny = 2\n",
        )
        self.assertEqual(added.changes[0].kind, "add_key")

        deleted = analyze_multiline_edit(
            "[a]\nx = 1\ny = 2\n",
            "[a]\nx = 1\n",
        )
        self.assertEqual(deleted.changes[0].kind, "delete_key")

    def test_section_rename_is_delete_plus_add(self):
        analysis = analyze_multiline_edit(
            "[old]\nx = 1\n",
            "[new]\nx = 1\n",
        )
        self.assertTrue(analysis.is_supported)
        kinds = {change.kind for change in analysis.changes}
        self.assertEqual(kinds, {"delete_section", "add_section"})
        self.assertTrue(
            any("переименование" in note for note in analysis.notes)
        )

    def test_insert_section_before_keys(self):
        analysis = analyze_multiline_edit(
            "[a]\nx = 1\n",
            "[a]\n[new]\nx = 1\n",
        )
        self.assertTrue(analysis.is_supported)
        kinds = {change.kind for change in analysis.changes}
        self.assertIn("delete_key", kinds)
        self.assertIn("add_section", kinds)

    def test_delete_section_block(self):
        analysis = analyze_multiline_edit(
            "[a]\nx = 1\n[b]\ny = 2\n",
            "[a]\nx = 1\n",
        )
        self.assertTrue(analysis.is_supported)
        self.assertEqual(analysis.changes[0].kind, "delete_section")

    def test_duplicate_key_conflict_is_resolved(self):
        analysis = analyze_multiline_edit(
            "[a]\nx = 1\n",
            "[a]\nx = 2\nx = 3\n",
        )
        self.assertTrue(analysis.is_supported)
        self.assertTrue(
            any(conflict.code == "duplicate_key" for conflict in analysis.conflicts)
        )
        self.assertTrue(
            any("выбрано последнее значение" in note for note in analysis.notes)
        )

    def test_include_change_is_blocking(self):
        analysis = analyze_multiline_edit(
            '#include "a.ltx"\n[a]\nx = 1\n',
            '#include "b.ltx"\n[a]\nx = 1\n',
        )
        self.assertTrue(analysis.has_errors)


class MultiLinePlanTest(unittest.TestCase):
    def _plan(self, baseline, edited, **extra):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "a.ltx"
            source.write_text(baseline, encoding="cp1251")
            forest = _Forest(d, [source])
            plan = PatchPlanner().plan_multiline_edit(
                source,
                baseline,
                edited,
                forest=forest,
                options=PatchOptions(
                    patch_mod="Test",
                    suffix="multi",
                    **extra,
                ),
            )
            return plan

    def test_multiple_changes_are_one_patch(self):
        plan = self._plan(
            "[a]\nx = 1\ny = 2\n",
            "[a]\nx = 3\ny = 4\n",
        )
        self.assertFalse(plan.has_errors)
        self.assertIn("x = 3", plan.preview)
        self.assertIn("y = 4", plan.preview)

        generated = DltxGenerator().generate(plan)
        self.assertTrue(ModelPatchVerifier().verify(generated).ok)

    def test_section_rename_patch(self):
        plan = self._plan(
            "[old]\nx = 1\n",
            "[new]\nx = 1\n",
            allow_delete_section=True,
        )
        self.assertFalse(plan.has_errors)
        self.assertIn("@[new]", plan.preview)
        self.assertIn("!![old]", plan.preview)

        generated = DltxGenerator().generate(plan)
        self.assertTrue(ModelPatchVerifier().verify(generated).ok)

    def test_insert_section_block_patch(self):
        plan = self._plan(
            "[a]\nx = 1\n",
            "[a]\n[new]\ny = 2\n",
        )
        self.assertFalse(plan.has_errors)
        self.assertIn("@[new]", plan.preview)
        self.assertIn("y = 2", plan.preview)

        generated = DltxGenerator().generate(plan)
        self.assertTrue(ModelPatchVerifier().verify(generated).ok)

    def test_delete_section_block_patch(self):
        plan = self._plan(
            "[a]\nx = 1\n[b]\ny = 2\n",
            "[a]\nx = 1\n",
            allow_delete_section=True,
        )
        self.assertFalse(plan.has_errors)
        self.assertIn("!![b]", plan.preview)

        generated = DltxGenerator().generate(plan)
        self.assertTrue(ModelPatchVerifier().verify(generated).ok)


if __name__ == "__main__":
    unittest.main()
