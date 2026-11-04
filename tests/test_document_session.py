# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.document_session import DocumentSession, FileDocumentTracker


class DocumentSessionTest(unittest.TestCase):
    def test_dirty_diff_and_revert(self):
        session = DocumentSession(
            Path("a.ltx"),
            "[a]\nx = 1\n",
        )

        self.assertFalse(session.is_dirty)
        self.assertTrue(session.diff().is_empty)

        changed = session.set_text("[a]\nx = 2\n")

        self.assertTrue(changed)
        self.assertTrue(session.is_dirty)
        self.assertEqual(session.revision, 1)

        diff = session.diff()
        self.assertEqual(len(diff.changes), 1)
        self.assertEqual(diff.changes[0].kind, "set_key")
        self.assertEqual(diff.changes[0].old_value, "1")
        self.assertEqual(diff.changes[0].new_value, "2")

        plan = session.plan()
        self.assertFalse(plan.has_errors)
        self.assertIn("x = 2", plan.preview)

        self.assertTrue(session.revert())
        self.assertFalse(session.is_dirty)
        self.assertTrue(session.diff().is_empty)

    def test_mark_saved_moves_baseline(self):
        session = DocumentSession(Path("a.ltx"), "[a]\nx = 1\n")
        session.set_text("[a]\nx = 2\n")
        session.mark_saved()

        self.assertFalse(session.is_dirty)
        self.assertEqual(session.baseline_text, "[a]\nx = 2\n")
        self.assertTrue(session.diff().is_empty)


class FileDocumentTrackerTest(unittest.TestCase):
    def test_detects_external_change(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "a.ltx"
            path.write_text("[a]\nx = 1\n", encoding="windows-1251")

            tracker = FileDocumentTracker(path)
            self.assertEqual(
                tracker.text.replace("\r\n", "\n"),
                "[a]\nx = 1\n",
            )
            self.assertFalse(tracker.refresh())

            path.write_text("[a]\nx = 2\n", encoding="windows-1251")

            self.assertTrue(tracker.refresh())
            self.assertEqual(
                tracker.text.replace("\r\n", "\n"),
                "[a]\nx = 2\n",
            )

            session = tracker.session()
            self.assertFalse(session.is_dirty)
            self.assertEqual(
                session.baseline_text.replace("\r\n", "\n"),
                "[a]\nx = 2\n",
            )


if __name__ == "__main__":
    unittest.main()
