# -*- coding: utf-8 -*-
"""Интеграция живого документа с resolved-видом."""

import unittest

from tests.support import SRC  # noqa: F401

from tests.qt_support import qapp
from ui.documents import ResolvedDocument
from ui.editor import ResolvedView

_app = qapp()


class ResolvedDocumentViewTest(unittest.TestCase):
    def _make(self):
        document = ResolvedDocument(
            "base.resolved:system.ltx",
            "system.ltx",
            "[Res]system.ltx",
            "[a]\nx = 1\n[b]\ny = 2\n",
            line_sources=(None, None, None, None, None),
        )
        view = ResolvedView()
        view.set_content(
            document.current_text,
            document.line_sources,
            document.root_path,
        )
        view.set_editable(True)
        view.bind_document(document)
        return document, view

    def test_typing_updates_document(self):
        document, view = self._make()
        view.editor.setText("[a]\nx = 3\n[b]\ny = 2\n")
        self.assertEqual(document.current_text, "[a]\nx = 3\n[b]\ny = 2\n")
        self.assertTrue(document.is_dirty)

    def test_document_edit_updates_view(self):
        document, view = self._make()
        self.assertTrue(
            document.apply_line_edit("replace", 2, "x = 9")
        )
        self.assertIn("x = 9", view.text())
        self.assertTrue(view.is_dirty)

    def test_insert_and_delete_update_view_and_sources(self):
        document, view = self._make()
        self.assertTrue(
            document.apply_line_edit("insert", 2, "z = 3")
        )
        self.assertIn("z = 3", view.text())
        self.assertEqual(
            len(view.line_sources),
            view.editor.lines(),
        )
        self.assertEqual(
            view.source_list._model.rowCount(),
            view.editor.lines(),
        )

        self.assertTrue(document.apply_line_edit("delete", 3))
        self.assertNotIn("z = 3", view.text())
        self.assertEqual(
            len(view.line_sources),
            view.editor.lines(),
        )
        self.assertEqual(
            view.source_list._model.rowCount(),
            view.editor.lines(),
        )

    def test_section_under_cursor_produces_item_request(self):
        _document, view = self._make()
        view.editor.setCursorPosition(2, 0)
        seen = []
        view.item_requested.connect(seen.append)
        view.open_item_under_cursor()
        self.assertEqual(seen, ["b"])

    def test_undo_after_document_edit_restores_view(self):
        document, view = self._make()
        self.assertTrue(
            document.apply_line_edit("replace", 2, "x = 9")
        )
        self.assertIn("x = 9", view.text())
        document.undo()
        self.assertIn("x = 1", view.text())


if __name__ == "__main__":
    unittest.main()
