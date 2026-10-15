# -*- coding: utf-8 -*-
"""Редактор кода на QScintilla."""

from pathlib import Path
from PyQt5.Qsci import QsciScintilla, QsciLexerXML, QsciLexerProperties
from PyQt5.QtGui import QFont, QColor
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel


class CodeEditor(QWidget):
    def __init__(self):
        super().__init__()

        self.current_file = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Заголовок с именем файла
        self.file_label = QLabel("Файл не открыт")
        self.file_label.setStyleSheet(
            "padding: 4px; background-color: #2D2D30; color: #D4D4D4;"
        )
        layout.addWidget(self.file_label)

        # Редактор QScintilla
        self.editor = QsciScintilla()
        self._setup_editor()
        layout.addWidget(self.editor)

        self.setLayout(layout)

    def _setup_editor(self):
        # Шрифт
        font = QFont("Cascadia Code", 10)
        self.editor.setFont(font)

        # Нумерация строк
        self.editor.setMarginType(0, QsciScintilla.NumberMargin)
        self.editor.setMarginWidth(0, "00000")
        self.editor.setMarginsBackgroundColor(QColor("#2D2D30"))
        self.editor.setMarginsForegroundColor(QColor("#9D9D9D"))

        # Тема оформления
        self.editor.setColor(QColor("#D4D4D4"))
        self.editor.setPaper(QColor("#1E1E1E"))
        self.editor.setCaretForegroundColor(QColor("#D4D4D4"))
        self.editor.setCaretLineBackgroundColor(QColor("#2A2D2E"))
        self.editor.setCaretLineVisible(True)

        # Отступы
        self.editor.setIndentationWidth(4)
        self.editor.setIndentationsUseTabs(False)

        # Перенос строк
        self.editor.setWrapMode(QsciScintilla.WrapNone)

    def open_file(self, file_path):
        path = Path(file_path)

        if not path.is_file():
            return

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except UnicodeDecodeError:
            content = path.read_text(encoding="windows-1251", errors="replace")

        self.editor.setReadOnly(False)
        self.editor.setText(content)
        self.current_file = str(path)
        self.file_label.setText(str(path))

        self._apply_lexer(path)

    def show_text(self, text, label, readonly=True):
        """
        Показать готовый текст (например, резолвленный через Resolver4).
        Не привязан к файлу на диске: сохранить его напрямую нельзя.
        """
        self.editor.setReadOnly(readonly)
        self.editor.setText(text)
        self.current_file = None
        self.file_label.setText(label)

    def _apply_lexer(self, path):
        suffix = path.suffix.lower()

        if suffix == '.xml':
            lexer = QsciLexerXML()
            lexer.setDefaultFont(self.editor.font())
            self.editor.setLexer(lexer)

        elif suffix == '.ltx':
            lexer = QsciLexerProperties()
            lexer.setDefaultFont(self.editor.font())
            self.editor.setLexer(lexer)

        else:
            self.editor.setLexer(None)

    def save_file(self):
        if not self.current_file:
            return

        content = self.editor.text()
        Path(self.current_file).write_text(content, encoding='utf-8')