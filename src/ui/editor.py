# -*- coding: utf-8 -*-
"""Редактор кода на QScintilla + поиск внутри файла."""

from pathlib import Path

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QColor, QFont, QKeySequence
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QShortcut,
    QVBoxLayout,
    QWidget,
)

from PyQt5.Qsci import (
    QsciScintilla,
    QsciLexerProperties,
    QsciLexerXML,
)


class EditorFindBar(QWidget):
    """Панель поиска ВНУТРИ файла (поисковые API QScintilla)."""

    def __init__(self, scintilla, parent=None):
        super().__init__(parent)

        self.scintilla = scintilla

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        layout.addWidget(QLabel("В файле:"))

        self.entry = QLineEdit()
        self.entry.setPlaceholderText(
            "поиск…  (Enter — след., Shift+Enter — пред., Esc — закрыть)"
        )
        layout.addWidget(self.entry, 1)

        self.status = QLabel("")
        layout.addWidget(self.status)

        btn_prev = QPushButton("↑")
        btn_prev.setFixedWidth(24)
        btn_prev.setToolTip("Предыдущее совпадение (Shift+F3)")
        btn_prev.clicked.connect(self.find_prev)
        layout.addWidget(btn_prev)

        btn_next = QPushButton("↓")
        btn_next.setFixedWidth(24)
        btn_next.setToolTip("Следующее совпадение (F3)")
        btn_next.clicked.connect(self.find_next)
        layout.addWidget(btn_next)

        btn_close = QPushButton("×")
        btn_close.setFixedWidth(24)
        btn_close.setToolTip("Закрыть поиск (Esc)")
        btn_close.clicked.connect(self.hide_and_restore)
        layout.addWidget(btn_close)

        self.entry.textChanged.connect(self._on_text_changed)
        self.entry.installEventFilter(self)

        self.hide()

    # -------------------------------------------------- клавиши
    def eventFilter(self, obj, ev):
        if obj is self.entry and ev.type() == QEvent.KeyPress:
            if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
                if ev.modifiers() & Qt.ShiftModifier:
                    self.find_prev()
                else:
                    self.find_next()
                return True

            if ev.key() == Qt.Key_Escape:
                self.hide_and_restore()
                return True

        return super().eventFilter(obj, ev)

    # -------------------------------------------------- логика
    def show_and_focus(self):
        self.show()
        self.entry.setFocus()
        self.entry.selectAll()

    def hide_and_restore(self):
        self.hide()
        self.scintilla.setFocus(Qt.OtherFocusReason)

    def _on_text_changed(self, _text):
        if self.entry.text():
            self.find_next()
        else:
            self.status.setText("")

    def find_next(self):
        self._find(forward=True)

    def find_prev(self):
        self._find(forward=False)

    def _find(self, forward):
        q = self.entry.text()
        if not q:
            return

        total = self.scintilla.text().lower().count(q.lower())
        if total == 0:
            self.status.setText("не найдено")
            return

        # re=False, case-sensitive=False, whole-word=False, wrap=True
        self.scintilla.findFirst(q, False, False, False, True, forward)
        self.status.setText("совпадений: %d" % total)


class CodeEditor(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.current_file = None

        self._init_ui()
        self._init_shortcuts()

    def _init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.file_label = QLabel("Файл не открыт")
        self.file_label.setStyleSheet(
            "padding: 4px; background-color: #2D2D30; color: #D4D4D4;"
        )
        layout.addWidget(self.file_label)

        self.editor = QsciScintilla()
        self._setup_editor()

        self.find_bar = EditorFindBar(self.editor)
        layout.addWidget(self.find_bar)
        layout.addWidget(self.editor, 1)

        self.setLayout(layout)

    def _init_shortcuts(self):
        # Клавиши ограничены редактором и его детьми:
        # срабатывают ТОЛЬКО когда фокус внутри редактора,
        # и не конфликтуют с поиском по дереву.
        QShortcut(
            QKeySequence("Ctrl+F"),
            self,
            context=Qt.WidgetWithChildrenShortcut,
            activated=self.show_find,
        )
        QShortcut(
            QKeySequence("F3"),
            self,
            context=Qt.WidgetWithChildrenShortcut,
            activated=self.find_bar.find_next,
        )
        QShortcut(
            QKeySequence("Shift+F3"),
            self,
            context=Qt.WidgetWithChildrenShortcut,
            activated=self.find_bar.find_prev,
        )

    def _setup_editor(self):
        font = QFont("Cascadia Code", 10)
        self.editor.setFont(font)

        self.editor.setMarginType(0, QsciScintilla.NumberMargin)
        self.editor.setMarginWidth(0, "00000")
        self.editor.setMarginsBackgroundColor(QColor("#2D2D30"))
        self.editor.setMarginsForegroundColor(QColor("#9D9D9D"))

        self.editor.setColor(QColor("#D4D4D4"))
        self.editor.setPaper(QColor("#1E1E1E"))

        self.editor.setCaretForegroundColor(QColor("#D4D4D4"))
        self.editor.setCaretLineBackgroundColor(QColor("#2A2D2E"))
        self.editor.setCaretLineVisible(True)

        self.editor.setIndentationWidth(4)
        self.editor.setIndentationsUseTabs(False)
        self.editor.setWrapMode(QsciScintilla.WrapNone)

    # -------------------------------------------------- публичные действия
    def show_find(self):
        self.find_bar.show_and_focus()

    def undo(self):
        self.editor.undo()

    def redo(self):
        self.editor.redo()

    # -------------------------------------------------- содержимое
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

        # Фокус в редактор: без этого внутренняя карта клавиш Scintilla
        # (undo/redo/копирование/поиск) "не работает" для пользователя.
        self.editor.setFocus(Qt.OtherFocusReason)

    def goto_line(self, line_number):
        """Перейти к указанной строке (1-based)."""
        if line_number < 1:
            return
        
        line_index = line_number - 1
        self.editor.setCursorPosition(line_index, 0)
        self.editor.ensureLineVisible(line_index)
        self.editor.setFocus(Qt.OtherFocusReason)
    
    def show_text(self, text, label, readonly=True):
        self.editor.setReadOnly(readonly)
        self.editor.setText(text)
        self.current_file = None
        self.file_label.setText(label)

        self.editor.setFocus(Qt.OtherFocusReason)

    def _apply_lexer(self, path: Path):
        suffix = path.suffix.lower()

        if suffix == ".xml":
            lexer = QsciLexerXML()
        elif suffix == ".ltx":
            lexer = QsciLexerProperties()
        else:
            lexer = None

        if lexer is not None:
            lexer.setDefaultFont(self.editor.font())
            lexer.setDefaultColor(QColor("#D4D4D4"))
            lexer.setDefaultPaper(QColor("#1E1E1E"))
            self.editor.setLexer(lexer)
        else:
            self.editor.setLexer(None)