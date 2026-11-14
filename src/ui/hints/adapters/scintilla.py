# -*- coding: utf-8 -*-
"""Адаптер QsciScintilla.

QScintilla-обёртка уже умеет работать с символьными line/index
координатами; байтовые позиции нужны только для ``SCI_POINT*``.
"""

from __future__ import annotations

from PyQt5.Qsci import QsciScintilla
from PyQt5.QtCore import QPoint

from .base import BaseAdapter


class ScintillaAdapter(BaseAdapter):
    kind = "scintilla"

    def __init__(self, editor):
        self._editor = editor
        self._revision = 0

    def widget(self):
        return self._editor

    def cursor_line(self):
        return int(self._editor.getCursorPosition()[0])

    def cursor_column(self):
        return int(self._editor.getCursorPosition()[1])

    def line_text(self, line):
        text = self._editor.text(int(line))
        return text.rstrip("\r\n")

    def selected_text(self):
        return self._editor.selectedText()

    def is_editable(self):
        return not self._editor.isReadOnly()

    def default_text_color(self):
        try:
            return self.color_tuple(self._editor.color())
        except Exception:
            return None

    def document_revision(self):
        return self._revision

    def local_sections(self):
        return None

    def replace_range(self, before, after, text, caret_offset=0):
        text = str(text or "")
        if "\n" in text or "\r" in text:
            raise ValueError("Подсказка не может вставлять перевод строки")
        line, column = self._editor.getCursorPosition()
        start = int(column) - int(before)
        end = int(column) + int(after)
        if start < 0 or end < start:
            raise ValueError("Некорректный диапазон замены")
        self._editor.beginUndoAction()
        try:
            self._editor.setSelection(line, start, line, end)
            self._editor.replaceSelectedText(text)
            target = start + len(text) + int(caret_offset or 0)
            self._editor.setCursorPosition(line, max(0, target))
        finally:
            self._editor.endUndoAction()

    def begin_edit(self):
        return None

    def end_edit(self, block=None):
        return None

    def cursor_rect_global(self):
        line, column = self._editor.getCursorPosition()
        position = self._editor.positionFromLineIndex(line, column)
        x = int(
            self._editor.SendScintilla(
                QsciScintilla.SCI_POINTXFROMPOSITION,
                0,
                position,
            )
        )
        y = int(
            self._editor.SendScintilla(
                QsciScintilla.SCI_POINTYFROMPOSITION,
                0,
                position,
            )
        )
        height = int(
            self._editor.SendScintilla(
                QsciScintilla.SCI_TEXTHEIGHT,
                line,
            )
        )
        height = max(1, height)
        point = self._editor.mapToGlobal(QPoint(x, y + height))
        return point.x(), point.y(), 1, height

    def connect_text_changed(self, callback):
        def _on_changed():
            self._revision += 1
            callback()

        self._editor.textChanged.connect(_on_changed)

    def connect_cursor_moved(self, callback):
        self._editor.cursorPositionChanged.connect(callback)

    def connect_scrolled(self, callback):
        self._editor.verticalScrollBar().valueChanged.connect(callback)

    def connect_destroyed(self, callback):
        self._editor.destroyed.connect(callback)
