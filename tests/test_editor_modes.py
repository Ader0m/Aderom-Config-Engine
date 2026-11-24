# -*- coding: utf-8 -*-
"""Режимы редактора: raw, draft, resolved, preview."""

import unittest

from tests.support import SRC  # noqa: F401

from PyQt5.QtWidgets import QWidget

from tests.qt_support import qapp
from ui.editor import CodeEditor, ResolvedView
from ui.editor_tabs import EditorTabs


class EditorModeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qapp()

    def test_raw_is_read_only_until_project_allows_edit(self):
        editor = CodeEditor()
        self.addCleanup(editor.deleteLater)
        editor.open_file("a.ltx", text="[a]\nx = 1\n")

        self.assertTrue(editor.editor.isReadOnly())
        self.assertEqual(editor.mode, "raw")

        editor.set_editable(True)
        self.assertFalse(editor.editor.isReadOnly())
        self.assertEqual(editor.mode, "draft")

    def test_dirty_is_explicit_and_cleared_by_mark_clean(self):
        editor = CodeEditor()
        self.addCleanup(editor.deleteLater)
        editor.set_editable(True)
        editor.open_file("a.ltx", text="[a]\nx = 1\n")

        editor.editor.setText("[a]\nx = 2\n")
        editor.flush_pending()
        self.assertTrue(editor.is_dirty)

        editor.mark_clean()
        self.assertFalse(editor.is_dirty)

    def test_resolved_view_mode(self):
        view = ResolvedView()
        self.addCleanup(view.deleteLater)
        self.assertEqual(view.mode, "resolved")

        view.set_editable(True)
        self.assertEqual(view.mode, "resolved")

    def test_editor_tabs_reports_tab_modes(self):
        tabs = EditorTabs()
        self.addCleanup(tabs.deleteLater)
        editor = tabs.ensure_default_tab()
        editor.open_file("a.ltx", text="[a]\nx = 1\n")
        tabs.open_tab("resolved.tab", "Resolved", ResolvedView())
        tabs.open_tab("preview.tab", "Preview", QWidget())

        self.assertEqual(tabs.tab_mode("ui.editor"), "raw")
        self.assertEqual(tabs.tab_mode("resolved.tab"), "resolved")
        self.assertEqual(tabs.tab_mode("preview.tab"), "preview")
        self.assertEqual(tabs.tab_mode("absent"), "")

        tabs.set_editable(True)
        self.assertEqual(tabs.tab_mode("ui.editor"), "draft")


if __name__ == "__main__":
    unittest.main()
