# -*- coding: utf-8 -*-
"""Адаптер QPlainTextEdit / QTextEdit / QLineEdit."""

from __future__ import annotations

from PyQt5.QtGui import QTextCursor
from PyQt5.QtWidgets import QLineEdit

from .base import BaseAdapter, utf16_to_py


class PlainTextAdapter(BaseAdapter):
    kind = "plain"

    def __init__(self, widget):
        self._widget = widget
        self._revision = 0
        self._changed_cb = None
        self._cursor_cb = None

    def widget(self):
        return self._widget

    # -------------------------------------------------- координаты
    def _is_line_edit(self):
        return isinstance(self._widget, QLineEdit)

    def cursor_line(self):
        if self._is_line_edit():
            return 0
        return self._widget.textCursor().blockNumber()

    def cursor_column(self):
        if self._is_line_edit():
            return int(self._widget.cursorPosition())
        cursor = self._widget.textCursor()
        block = cursor.block()
        return utf16_to_py(block.text(), cursor.positionInBlock())

    def line_text(self, line):
        if self._is_line_edit():
            return self._widget.text() if int(line) == 0 else ""
        block = self._widget.document().findBlockByNumber(int(line))
        return block.text() if block.isValid() else ""

    def selected_text(self):
        if self._is_line_edit():
            return self._widget.selectedText()
        return self._widget.textCursor().selectedText()

    def is_editable(self):
        return not self._widget.isReadOnly()

    def default_text_color(self):
        return self.color_tuple(self._widget.palette().text().color())

    def document_revision(self):
        return self._revision

    # -------------------------------------------------- правка
    def _cursor_positions(self):
        if self._is_line_edit():
            return int(self._widget.cursorPosition())
        return self._widget.textCursor().position()

    def replace_range(self, before, after, text, caret_offset=0):
        text = str(text or "")
        if "\n" in text or "\r" in text:
            raise ValueError("Подсказка не может вставлять перевод строки")
        line_prefix = self.line_prefix()
        line_suffix = self.line_suffix()
        before_text = line_prefix[-before:] if before else ""
        after_text = line_suffix[:after] if after else ""
        start = self._cursor_positions() - len(
            before_text.encode("utf-16-le")
        ) // 2
        end = self._cursor_positions() + len(
            after_text.encode("utf-16-le")
        ) // 2

        if self._is_line_edit():
            self._widget.setSelection(start, max(0, end - start))
            self._widget.insert(text)
            base = start + len(text.encode("utf-16-le")) // 2
            self._widget.setCursorPosition(
                self._caret_position(base, text, caret_offset, line_suffix,
                                     after)
            )
            return

        cursor = self._widget.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        cursor.insertText(text)
        base = start + len(text.encode("utf-16-le")) // 2
        cursor.setPosition(
            self._caret_position(base, text, caret_offset, line_suffix, after)
        )
        self._widget.setTextCursor(cursor)

    @staticmethod
    def _caret_position(base, text, offset, line_suffix, after):
        offset = int(offset or 0)
        if offset == 0:
            return base
        if offset < 0:
            tail = text[offset:]
            return base - (len(tail.encode("utf-16-le")) // 2)
        rest = line_suffix[after: after + offset]
        return base + (len(rest.encode("utf-16-le")) // 2)

    def begin_edit(self):
        if self._is_line_edit():
            return None
        cursor = self._widget.textCursor()
        cursor.beginEditBlock()
        return cursor

    def end_edit(self, block=None):
        if self._is_line_edit():
            return None
        cursor = block or self._widget.textCursor()
        try:
            cursor.endEditBlock()
        except Exception:
            pass

    # -------------------------------------------------- экран
    def cursor_rect_global(self):
        if self._is_line_edit():
            rect = self._widget.cursorRect()
            point = self._widget.mapToGlobal(rect.bottomLeft())
        else:
            rect = self._widget.cursorRect()
            point = self._widget.viewport().mapToGlobal(rect.bottomLeft())
        return point.x(), point.y(), max(1, rect.width()), max(1, rect.height())

    # -------------------------------------------------- сигналы
    def connect_text_changed(self, callback):
        self._changed_cb = callback

        def _on_changed():
            self._revision += 1
            callback()

        self._widget.textChanged.connect(_on_changed)

    def connect_cursor_moved(self, callback):
        self._cursor_cb = callback
        self._widget.cursorPositionChanged.connect(callback)

    def connect_scrolled(self, callback):
        scrollbar = getattr(self._widget, "verticalScrollBar", None)
        if callable(scrollbar):
            scrollbar().valueChanged.connect(callback)

    def connect_destroyed(self, callback):
        self._widget.destroyed.connect(callback)
