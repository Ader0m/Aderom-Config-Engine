# -*- coding: utf-8 -*-
"""Редактор кода на QScintilla + поиск внутри файла.

Все цвета и шрифты берутся из ui.theme.PALETTE — единственного источника
темы. QSS не управляет стилями лексера Scintilla, поэтому палитра
применяется программно: setFont/setColor/setPaper на ВСЕ стили лексера
(иначе просвечивают штатные дефолты QsciLexerProperties: бирюза #007F7F
у Comment, светлая полоса #E0F0F0 у Section, Comic Sans MS у Comment),
плюс точечные акценты syn_* по стилям.
"""

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

try:  # запуск с корня проекта (src в sys.path)
    from ui.theme import PALETTE
except ImportError:  # запуск как пакета
    from .theme import PALETTE


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
                if ev.key() == Qt.Key_Enter or ev.modifiers() & Qt.ShiftModifier:
                    if ev.modifiers() & Qt.ShiftModifier:
                        self.find_prev()
                    else:
                        self.find_next()
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
        # Цвета — из палитры, без hex-литералов в UI-коде.
        self.file_label.setStyleSheet(
            "padding: 4px; background-color: {bg}; color: {fg};".format(
                bg=PALETTE.bg_titlebar, fg=PALETTE.text
            )
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

    # -------------------------------------------------- оформление
    def _editor_font(self) -> QFont:
        """Только явный point-based шрифт из палитры.

        НЕ использовать self.editor.font(): шрифт виджета может быть
        переопределён QSS, а пиксельный размер даёт pointSizeF() == -1,
        который Scintilla превращает в микроскопический текст
        (SCI_STYLESETSIZEFRACTIONAL).
        """
        return QFont(PALETTE.editor_font_family, PALETTE.editor_font_size_pt)

    def _setup_editor(self):
        font = self._editor_font()
        self.editor.setFont(font)

        self.editor.setMarginType(0, QsciScintilla.NumberMargin)
        self.editor.setMarginWidth(0, "00000")
        self.editor.setMarginsFont(font)
        self.editor.setMarginsBackgroundColor(QColor(PALETTE.ed_margin_bg))
        self.editor.setMarginsForegroundColor(QColor(PALETTE.ed_margin_fg))

        self.editor.setColor(QColor(PALETTE.syn_default))
        self.editor.setPaper(QColor(PALETTE.bg_main))

        self.editor.setCaretForegroundColor(QColor(PALETTE.ed_caret))
        self.editor.setCaretLineBackgroundColor(QColor(PALETTE.ed_caret_line))
        self.editor.setCaretLineVisible(True)

        self.editor.setIndentationWidth(4)
        self.editor.setIndentationsUseTabs(False)
        self.editor.setWrapMode(QsciScintilla.WrapNone)

    def _apply_lexer(self, path: Path):
        suffix = path.suffix.lower()

        if suffix == ".xml":
            lexer = QsciLexerXML()
        elif suffix == ".ltx":
            lexer = QsciLexerProperties()
        else:
            lexer = None

        if lexer is None:
            self.editor.setLexer(None)
            return

        font = self._editor_font()
        paper = QColor(PALETTE.bg_main)
        fg = QColor(PALETTE.syn_default)

        # Дефолты лексера.
        lexer.setDefaultFont(font)
        lexer.setDefaultColor(fg)
        lexer.setDefaultPaper(paper)

        # Переопределения ШТАТНЫХ per-style дефолтов лексера
        # (Comment=Comic Sans/#007F7F, Section=#E0F0F0 и т.д.).
        # style == -1 означает "все стили".
        lexer.setFont(font)
        lexer.setColor(fg)
        lexer.setPaper(paper)

        if isinstance(lexer, QsciLexerProperties):
            lexer.setColor(QColor(PALETTE.syn_comment), QsciLexerProperties.Comment)
            lexer.setColor(QColor(PALETTE.syn_section), QsciLexerProperties.Section)
            lexer.setColor(QColor(PALETTE.syn_key), QsciLexerProperties.Key)
            lexer.setColor(QColor(PALETTE.syn_assign), QsciLexerProperties.Assignment)
            lexer.setColor(QColor(PALETTE.syn_value), QsciLexerProperties.DefaultValue)
        elif isinstance(lexer, QsciLexerXML):
            lexer.setColor(QColor(PALETTE.syn_section), QsciLexerXML.Tag)
            lexer.setColor(QColor(PALETTE.syn_section), QsciLexerXML.TagEnd)
            lexer.setColor(QColor(PALETTE.syn_key), QsciLexerXML.Attribute)
            lexer.setColor(QColor(PALETTE.syn_value), QsciLexerXML.DoubleQuotedString)
            lexer.setColor(QColor(PALETTE.syn_value), QsciLexerXML.SingleQuotedString)
            lexer.setColor(QColor(PALETTE.syn_comment), QsciLexerXML.Comment)

        self.editor.setLexer(lexer)

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