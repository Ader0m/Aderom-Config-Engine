# -*- coding: utf-8 -*-
"""Тесты живого resolved-документа (без Qt)."""

import unittest

from tests.support import SRC  # noqa: F401

from ui.documents import ResolvedDocument, apply_line_edit


class _Events:
    def __init__(self):
        self.published = []

    def publish(self, name, payload=None):
        self.published.append((name, payload))


class ResolvedDocumentTest(unittest.TestCase):
    def _make(self, text=None, persist=None):
        self.events = _Events()
        self.persisted = []

        def default_persist(doc):
            self.persisted.append(doc.current_text)
            return True

        doc = ResolvedDocument(
            "res.test",
            "system.ltx",
            "RESOLVED: system.ltx",
            text if text is not None else "[a]\nx = 1\n[b]\ny = 2\n",
            line_sources=(None, None, None, None, None),
            events=self.events,
            persist=persist or default_persist,
        )
        return doc

    def test_initial_state_and_snapshot(self):
        doc = self._make()
        self.assertFalse(doc.is_dirty)
        self.assertFalse(doc.has_changes)
        self.assertEqual(doc.line_count, 5)
        snapshot = doc.snapshot()
        self.assertEqual(snapshot.text, doc.current_text)
        self.assertEqual(len(snapshot.line_sources), doc.line_count)

    def test_replace_text_persists_and_publishes(self):
        doc = self._make()
        changed = doc.replace_text("[a]\nx = 2\n")
        self.assertTrue(changed)
        self.assertTrue(doc.is_dirty)
        self.assertEqual(doc.revision, 1)
        self.assertEqual(self.persisted[-1], "[a]\nx = 2\n")
        self.assertEqual(len(self.events.published), 1)
        name, payload = self.events.published[0]
        self.assertEqual(name, "resolved.document.changed")
        self.assertEqual(payload.tab_id, "res.test")
        self.assertEqual(payload.revision, 1)

    def test_replace_same_text_is_noop(self):
        doc = self._make()
        self.assertFalse(doc.replace_text(doc.current_text))
        self.assertEqual(doc.revision, 0)
        self.assertEqual(self.persisted, [])

    def test_apply_line_edit_replace(self):
        doc = self._make()
        self.assertTrue(doc.apply_line_edit("replace", 2, "x = 9"))
        self.assertEqual(doc.current_text, "[a]\nx = 9\n[b]\ny = 2\n")
        self.assertEqual(doc.line_sources[1], None)
        self.assertEqual(doc.revision, 1)

    def test_apply_line_edit_insert_and_delete_sources(self):
        doc = self._make()
        self.assertTrue(doc.apply_line_edit("insert", 2, "z = 3"))
        self.assertEqual(
            doc.current_text,
            "[a]\nx = 1\nz = 3\n[b]\ny = 2\n",
        )
        self.assertEqual(len(doc.line_sources), doc.line_count)

        self.assertTrue(doc.apply_line_edit("delete", 3))
        self.assertEqual(
            doc.current_text,
            "[a]\nx = 1\n[b]\ny = 2\n",
        )
        self.assertEqual(len(doc.line_sources), doc.line_count)

    def test_apply_line_edit_invalid_line_is_rolled_back(self):
        doc = self._make()
        before = doc.current_text
        self.assertFalse(doc.apply_line_edit("replace", 99, "x = 0"))
        self.assertEqual(doc.current_text, before)
        self.assertEqual(doc.revision, 0)

    def test_persist_failure_rolls_back_atomic_edit(self):
        def fail(_doc):
            return False

        doc = self._make(persist=fail)
        before = doc.current_text
        self.assertFalse(doc.apply_line_edit("replace", 2, "x = 9"))
        self.assertEqual(doc.current_text, before)
        self.assertEqual(doc.revision, 0)
        self.assertTrue(doc.persist_error)
        self.assertEqual(self.events.published, [])

    def test_sections_index_and_field_lookup(self):
        doc = self._make()
        self.assertEqual(doc.section_at_line(1), "a")
        self.assertEqual(doc.section_at_line(2), "a")
        self.assertEqual(doc.section_at_line(3), "b")
        self.assertIsNone(doc.section_at_line(0))
        self.assertEqual(doc.section_bounds("a"), (1, 3))
        self.assertEqual(doc.section_bounds("b"), (3, 6))
        self.assertEqual(doc.field_line("a", "x"), 2)
        self.assertEqual(doc.field_line("b", "y"), 4)
        self.assertEqual(doc.field_line("b", "x"), 0)

    def test_section_block_returns_text_and_offset(self):
        doc = self._make()
        block, offset = doc.section_block("b")
        self.assertEqual(offset, 2)
        self.assertTrue(block.startswith("[b]"))
        self.assertIn("y = 2", block)

    def test_section_at_line_uses_nearest_header(self):
        doc = self._make("[a]\nx = 1\n\n; comment\ny = 2\n")
        self.assertEqual(doc.section_at_line(4), "a")
        self.assertEqual(doc.section_at_line(5), "a")

    def test_apply_field_edit_existing(self):
        doc = self._make()
        self.assertTrue(doc.apply_field_edit("a", "x", "42"))
        self.assertEqual(doc.current_text, "[a]\nx = 42\n[b]\ny = 2\n")
        self.assertEqual(doc.section_at_line(2), "a")

    def test_apply_field_edit_inserts_missing_field(self):
        doc = self._make()
        self.assertTrue(doc.apply_field_edit("a", "z", "3"))
        self.assertEqual(
            doc.current_text,
            "[a]\nx = 1\nz = 3\n[b]\ny = 2\n",
        )

    def test_apply_field_edit_unknown_section(self):
        doc = self._make()
        self.assertFalse(doc.apply_field_edit("nope", "x", "1"))

    def test_delete_field(self):
        doc = self._make()
        self.assertTrue(doc.delete_field("a", "x"))
        self.assertEqual(doc.current_text, "[a]\n[b]\ny = 2\n")

    def test_rename_field(self):
        doc = self._make()
        self.assertTrue(doc.rename_field("a", "x", "z", "9"))
        self.assertEqual(doc.current_text, "[a]\nz = 9\n[b]\ny = 2\n")

    def test_field_line_ignores_multiline_value_contents(self):
        doc = self._make(
            '[a]\ndesc = "line1\n  kind = not_a_field\nline3"\n'
            "kind = i_food\n"
        )
        self.assertEqual(doc.field_line("a", "kind"), 5)
        self.assertTrue(doc.apply_field_edit("a", "kind", "i_med"))
        self.assertIn("kind = i_med", doc.current_text)
        self.assertIn("kind = not_a_field", doc.current_text)

    def test_subscription_and_unsubscribe(self):
        doc = self._make()
        seen = []
        sub = doc.subscribe(lambda change: seen.append(change.revision))
        doc.replace_text("[a]\nx = 2\n")
        self.assertEqual(seen, [1])
        sub.unsubscribe()
        doc.replace_text("[a]\nx = 3\n")
        self.assertEqual(seen, [1])

    def test_retain_release_and_drop(self):
        doc = self._make()
        self.assertTrue(doc.can_drop())
        doc.retain()
        self.assertFalse(doc.can_drop())
        doc.release()
        self.assertTrue(doc.can_drop())

    def test_replace_text_focus_line_publishes_section(self):
        doc = self._make()
        seen = []
        doc.subscribe(lambda change: seen.append(change))
        doc.replace_text(
            "[a]\nx = 2\n[b]\ny = 2\n",
            focus_line=4,
        )
        self.assertEqual(seen[-1].sections, ())
        self.assertEqual(seen[-1].focus_line, 4)

    def test_undo_without_view(self):
        doc = self._make()
        self.assertTrue(doc.apply_line_edit("replace", 2, "x = 9"))
        self.assertTrue(doc.undo())
        self.assertEqual(doc.current_text, "[a]\nx = 1\n[b]\ny = 2\n")
        self.assertTrue(doc.redo())
        self.assertEqual(doc.current_text, "[a]\nx = 9\n[b]\ny = 2\n")

    def test_undo_insert_and_delete_without_view(self):
        doc = self._make()
        before = doc.current_text
        self.assertTrue(doc.apply_line_edit("insert", 2, "z = 3"))
        self.assertTrue(doc.undo())
        self.assertEqual(doc.current_text, before)

        self.assertTrue(doc.apply_line_edit("delete", 2))
        self.assertTrue(doc.undo())
        self.assertEqual(doc.current_text, before)

    def test_undo_delete_restores_line_source(self):
        source = object()
        doc = ResolvedDocument(
            "res.test",
            "system.ltx",
            "[Res]system.ltx",
            "[a]\nx = 1\ny = 2\n",
            line_sources=(None, source, None, None),
        )
        self.assertTrue(doc.delete_field("a", "x"))
        self.assertTrue(doc.undo())
        self.assertIs(doc.line_sources[1], source)


class ApplyLineEditHelperTest(unittest.TestCase):
    def test_rejects_multiline_replacement(self):
        with self.assertRaises(ValueError):
            apply_line_edit("[a]\n", (None, None), "replace", 1, "a\nb")

    def test_insert_zero_inserts_at_top(self):
        text, sources = apply_line_edit(
            "[a]\nx = 1\n",
            (None, None),
            "insert",
            0,
            "; header",
        )
        self.assertEqual(text, "; header\n[a]\nx = 1\n")
        self.assertEqual(len(sources), 4)


if __name__ == "__main__":
    unittest.main()
