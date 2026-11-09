# -*- coding: utf-8 -*-
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.patching.ltx.diff import DocumentChange
from core.patching.ltx.merge import merge_patch_plans
from core.patching.models import PatchPlan, PatchTarget


def _plan(source, value, key="cost", root="C:/game/system.ltx"):
    root = Path(root)
    return PatchPlan(
        source_path=Path(source),
        target=PatchTarget(
            source_path=Path(source),
            root_path=root,
            candidates=(root,),
        ),
        patch_mod="Test",
        patch_file_rel=Path("mod_system_~ace.ltx"),
        changes=(
            DocumentChange(
                kind="set_key",
                section="w",
                key=key,
                new_value=value,
            ),
        ),
    )


class PatchMergeTest(unittest.TestCase):
    def test_same_key_uses_last_plan_and_warns(self):
        merged = merge_patch_plans(
            [
                _plan("C:/game/a.ltx", "200"),
                _plan("C:/game/b.ltx", "300"),
            ]
        )

        self.assertEqual(len(merged.changes), 1)
        self.assertEqual(merged.changes[0].new_value, "300")
        self.assertTrue(
            any(
                issue.code == "merge_key_conflict"
                for issue in merged.warnings
            )
        )
        self.assertIn("cost = 300", merged.preview)
        self.assertIsNone(merged.diff)

    def test_disjoint_keys_are_merged_without_conflict(self):
        merged = merge_patch_plans(
            [
                _plan("C:/game/a.ltx", "200", key="cost"),
                _plan("C:/game/b.ltx", "x", key="name"),
            ]
        )

        self.assertEqual(
            [(change.key, change.new_value) for change in merged.changes],
            [("cost", "200"), ("name", "x")],
        )
        self.assertEqual(merged.warnings, ())
        self.assertIn("cost = 200", merged.preview)
        self.assertIn("name = x", merged.preview)

    def test_plans_from_different_roots_are_rejected(self):
        with self.assertRaises(ValueError):
            merge_patch_plans(
                [
                    _plan(
                        "C:/game/a.ltx",
                        "200",
                        root="C:/game/system.ltx",
                    ),
                    _plan(
                        "C:/game/b.ltx",
                        "300",
                        root="C:/game/other.ltx",
                    ),
                ]
            )


if __name__ == "__main__":
    unittest.main()
