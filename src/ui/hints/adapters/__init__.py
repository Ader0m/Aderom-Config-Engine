# -*- coding: utf-8 -*-
"""Выбор адаптера по типу текстового виджета."""

from __future__ import annotations

from PyQt5.Qsci import QsciScintilla
from PyQt5.QtWidgets import QLineEdit, QPlainTextEdit, QTextEdit

from .plain import PlainTextAdapter
from .scintilla import ScintillaAdapter


def create_adapter(widget):
    if isinstance(widget, QsciScintilla):
        return ScintillaAdapter(widget)
    if isinstance(widget, (QPlainTextEdit, QTextEdit, QLineEdit)):
        return PlainTextAdapter(widget)
    return None


__all__ = [
    "PlainTextAdapter",
    "ScintillaAdapter",
    "create_adapter",
]
