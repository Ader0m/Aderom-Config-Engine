# -*- coding: utf-8 -*-
"""Базовые помощники адаптеров редакторов."""

from __future__ import annotations


def utf16_len(text):
    """Длина строки в UTF-16 code units (модель QTextCursor)."""
    return len(str(text).encode("utf-16-le")) // 2


def utf16_to_py(text, units):
    """Индекс Python-строки по UTF-16 смещению."""
    text = str(text)
    wanted = max(0, int(units))
    if wanted <= 0:
        return 0
    consumed = 0
    for index, char in enumerate(text):
        size = 2 if ord(char) > 0xFFFF else 1
        if consumed + size > wanted:
            return index
        consumed += size
        if consumed == wanted:
            return index + 1
    return len(text)


def py_to_utf16(text, index):
    """UTF-16 смещение по индексу Python-строки."""
    text = str(text)
    index = max(0, min(int(index), len(text)))
    return utf16_len(text[:index])


class BaseAdapter:
    kind = ""

    def widget(self):
        raise NotImplementedError

    def cursor_line(self):
        raise NotImplementedError

    def cursor_column(self):
        raise NotImplementedError

    def line_text(self, line):
        raise NotImplementedError

    def line_prefix(self):
        line = self.cursor_line()
        text = self.line_text(line)
        return text[: self.cursor_column()]

    def line_suffix(self):
        line = self.cursor_line()
        text = self.line_text(line)
        return text[self.cursor_column():]

    def selected_text(self):
        raise NotImplementedError

    def is_editable(self):
        raise NotImplementedError

    def default_text_color(self):
        return None

    def document_revision(self):
        return 0

    def local_sections(self):
        return None

    def begin_edit(self):
        return None

    def end_edit(self):
        return None

    def replace_range(self, before, after, text, caret_offset=0):
        raise NotImplementedError

    def cursor_rect_global(self):
        raise NotImplementedError

    def connect_text_changed(self, callback):
        return None

    def connect_cursor_moved(self, callback):
        return None

    def connect_scrolled(self, callback):
        return None

    def connect_destroyed(self, callback):
        return None

    @staticmethod
    def color_tuple(color):
        if color is None:
            return None
        try:
            return (
                int(color.red()) & 0xFF,
                int(color.green()) & 0xFF,
                int(color.blue()) & 0xFF,
                int(color.alpha()) & 0xFF,
            )
        except Exception:
            return None
