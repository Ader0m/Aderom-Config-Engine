# -*- coding: utf-8 -*-
"""Адаптеры движка подсказок."""

import unittest

from tests.qt_support import qapp
from tests.support import SRC  # noqa: F401

from PyQt5.Qsci import QsciScintilla
from PyQt5.QtWidgets import QPlainTextEdit

from ui.hints.adapters import create_adapter

_app = qapp()


class PlainTextAdapterTest(unittest.TestCase):
    def test_cyrillic_positions_and_undo(self):
        widget = QPlainTextEdit()
        widget.setPlainText("Привет %c[")
        cursor = widget.textCursor()
        cursor.setPosition(len("Привет %c["))
        widget.setTextCursor(cursor)
        adapter = create_adapter(widget)
        self.assertEqual(adapter.cursor_line(), 0)
        self.assertEqual(adapter.cursor_column(), len("Привет %c["))
        self.assertEqual(adapter.line_prefix(), "Привет %c[")

        block = adapter.begin_edit()
        try:
            adapter.replace_range(0, 0, "gray]", 0)
        finally:
            adapter.end_edit(block)
        self.assertEqual(widget.toPlainText(), "Привет %c[gray]")
        widget.undo()
        self.assertEqual(widget.toPlainText(), "Привет %c[")

    def test_astral_column(self):
        widget = QPlainTextEdit()
        text = "😀%c["
        widget.setPlainText(text)
        cursor = widget.textCursor()
        cursor.setPosition(len(text.encode("utf-16-le")) // 2)
        widget.setTextCursor(cursor)
        adapter = create_adapter(widget)
        self.assertEqual(adapter.cursor_column(), len(text))

    def test_read_only(self):
        widget = QPlainTextEdit()
        widget.setReadOnly(True)
        adapter = create_adapter(widget)
        self.assertFalse(adapter.is_editable())


class LineEditAdapterTest(unittest.TestCase):
    def test_replaces_selection(self):
        from PyQt5.QtWidgets import QLineEdit

        widget = QLineEdit("abcdef")
        widget.setCursorPosition(4)
        adapter = create_adapter(widget)
        adapter.replace_range(2, 0, "X", 0)
        self.assertEqual(widget.text(), "abXef")
        self.assertEqual(widget.cursorPosition(), 3)


class ScintillaAdapterTest(unittest.TestCase):
    def test_cyrillic_positions_and_undo(self):
        editor = QsciScintilla()
        editor.setText("Привет %c[")
        editor.setCursorPosition(0, len("Привет %c["))
        adapter = create_adapter(editor)
        self.assertEqual(adapter.cursor_column(), len("Привет %c["))
        self.assertEqual(adapter.line_prefix(), "Привет %c[")

        adapter.replace_range(0, 0, "gray]", 0)
        self.assertEqual(editor.text(), "Привет %c[gray]")
        editor.undo()
        self.assertEqual(editor.text(), "Привет %c[")

    def test_read_only(self):
        editor = QsciScintilla()
        editor.setReadOnly(True)
        adapter = create_adapter(editor)
        self.assertFalse(adapter.is_editable())


if __name__ == "__main__":
    unittest.main()
