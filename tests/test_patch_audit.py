# -*- coding: utf-8 -*-
"""Файловый аудит записей патч-мода."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests.support import SRC  # noqa: F401

from app.patching.audit import PatchAuditLog
from core.patching.commit import PatchCommitResult


class PatchAuditLogTest(unittest.TestCase):
    def test_for_session_creates_new_file(self):
        with tempfile.TemporaryDirectory() as d:
            audit = PatchAuditLog.for_session(Path(d))
            self.assertEqual(audit.path.parent, Path(d))
            self.assertTrue(audit.path.name.startswith("patch-audit-"))
            self.assertTrue(audit.path.is_file())
            self.assertEqual(audit.path.read_text(encoding="utf-8"), "")

    def test_writes_one_json_line_per_result(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "logs" / "patch-audit.log"
            audit = PatchAuditLog(path)
            first = PatchCommitResult(
                mod_dir=Path("mods") / "ACE Patch",
                paths=(Path("mods/ACE Patch/a.ltx"),),
                plan_id="plan-1",
                snapshot_id="SNAP-1",
                confirmed_risks=("danger",),
            )
            second = PatchCommitResult(
                mod_dir=Path("mods") / "ACE Patch",
                paths=(Path("mods/ACE Patch/b.ltx"),),
                plan_id="plan-2",
                snapshot_id="SNAP-2",
            )

            audit(first)
            audit(second)
            audit(
                replace(
                    first,
                    status="rolled_back",
                    reason="cancelled",
                )
            )

            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 3)
            records = [json.loads(line) for line in lines]
            self.assertEqual(records[0]["plan_id"], "plan-1")
            self.assertEqual(records[0]["snapshot_id"], "SNAP-1")
            self.assertEqual(records[0]["confirmed_risks"], ["danger"])
            self.assertEqual(records[0]["status"], "committed")
            self.assertEqual(records[1]["status"], "committed")
            self.assertEqual(
                records[0]["paths"],
                [str(Path("mods/ACE Patch/a.ltx"))],
            )
            self.assertEqual(records[1]["plan_id"], "plan-2")
            self.assertEqual(records[1]["confirmed_risks"], [])
            self.assertEqual(records[2]["status"], "rolled_back")
            self.assertEqual(records[2]["reason"], "cancelled")


if __name__ == "__main__":
    unittest.main()
