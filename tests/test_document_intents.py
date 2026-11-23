# -*- coding: utf-8 -*-
"""ResolvedDocumentSession: единый вход правок и сохранение в проект."""

import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from app.documents import (
    DeleteFieldIntent,
    ResolvedDocumentSession,
    FieldEditIntent,
    InsertFieldIntent,
    LineEditIntent,
    RedoIntent,
    RenameFieldIntent,
    ReplaceTextIntent,
    ResetIntent,
    UndoIntent,
)


def _document(root: Path, **kwargs) -> ResolvedDocumentSession:
    return ResolvedDocumentSession(
        "tab",
        root,
        "ROOT",
        "[a]\nx = 1\n",
        **kwargs,
    )


class DocumentIntentTest(unittest.TestCase):
    def test_line_edit_intent(self):
        with tempfile.TemporaryDirectory() as d:
            document = _document(Path(d))

            changed = document.apply(
                LineEditIntent("replace", 2, "x = 2")
            )

            self.assertTrue(changed)
            self.assertEqual(document.current_text, "[a]\nx = 2\n")
            self.assertEqual(document.revision, 1)

    def test_field_edit_intent(self):
        with tempfile.TemporaryDirectory() as d:
            document = _document(Path(d))

            changed = document.apply(
                FieldEditIntent("a", "x", "3")
            )

            self.assertTrue(changed)
            self.assertEqual(document.current_text, "[a]\nx = 3\n")

    def test_rename_field_intent(self):
        with tempfile.TemporaryDirectory() as d:
            document = _document(Path(d))

            changed = document.apply(
                RenameFieldIntent("a", "x", "y", "3")
            )

            self.assertTrue(changed)
            self.assertEqual(document.current_text, "[a]\ny = 3\n")

    def test_delete_and_insert_field_intents(self):
        with tempfile.TemporaryDirectory() as d:
            document = _document(Path(d))

            self.assertTrue(
                document.apply(DeleteFieldIntent("a", "x"))
            )
            self.assertNotIn("x = 1", document.current_text)

            self.assertTrue(
                document.apply(InsertFieldIntent("a", "z", "4"))
            )
            self.assertIn("z = 4", document.current_text)

    def test_replace_text_intent(self):
        with tempfile.TemporaryDirectory() as d:
            document = _document(Path(d))

            changed = document.apply(
                ReplaceTextIntent("[b]\ny = 1\n")
            )

            self.assertTrue(changed)
            self.assertEqual(document.current_text, "[b]\ny = 1\n")

    def test_unknown_intent_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            document = _document(Path(d))
            with self.assertRaises(TypeError):
                document.apply(object())

    def test_prepared_line_edit_intent(self):
        with tempfile.TemporaryDirectory() as d:
            document = _document(Path(d))

            changed = document.apply(
                LineEditIntent(
                    "replace",
                    2,
                    "x = 2",
                    section="a",
                    prepared=("[a]\nx = 2\n", (None, None)),
                )
            )

            self.assertTrue(changed)
            self.assertEqual(document.current_text, "[a]\nx = 2\n")

    def test_undo_and_redo_intents(self):
        with tempfile.TemporaryDirectory() as d:
            document = _document(Path(d))
            document.apply(FieldEditIntent("a", "x", "2"))

            self.assertTrue(document.apply(UndoIntent()))
            self.assertEqual(document.current_text, "[a]\nx = 1\n")
            self.assertTrue(document.apply(RedoIntent()))
            self.assertEqual(document.current_text, "[a]\nx = 2\n")

    def test_reset_intent(self):
        with tempfile.TemporaryDirectory() as d:
            document = _document(Path(d))
            document.apply(FieldEditIntent("a", "x", "2"))

            changed = document.apply(
                ResetIntent(
                    "[a]\nx = 1\n",
                    saved_text="[a]\nx = 1\n",
                    persist=False,
                )
            )

            self.assertTrue(changed)
            self.assertFalse(document.is_dirty)


class DocumentSaveTest(unittest.TestCase):
    def test_save_uses_project_callback_and_marks_saved(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            document = _document(
                Path(d),
                save=lambda current: calls.append(current) or True,
            )

            document.apply(FieldEditIntent("a", "x", "9"))
            self.assertTrue(document.is_dirty)

            self.assertTrue(document.save())
            self.assertEqual(calls, [document])
            self.assertFalse(document.is_dirty)
            self.assertEqual(document.saved_text, "[a]\nx = 9\n")

    def test_subscription_receives_changes(self):
        with tempfile.TemporaryDirectory() as d:
            document = _document(Path(d))
            seen = []
            subscription = document.subscribe(
                lambda sections, **kwargs: seen.append(sections)
            )

            document.apply(FieldEditIntent("a", "x", "8"))
            subscription.unsubscribe()

            self.assertTrue(seen)
            self.assertEqual(document.subscriber_count, 0)


if __name__ == "__main__":
    unittest.main()
