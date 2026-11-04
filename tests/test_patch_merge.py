# -*- coding: utf-8 -*-
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.document_diff import DocumentChange
from core.patch_merge import merge_patch_plans
from core.patch_models import PatchPlan, PatchTarget


def _plan(source, value):
    root = Path("C:/game/system.ltx")
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
                key="cost",
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


if __name__ == "__main__":
    unittest.main()
