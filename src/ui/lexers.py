# -*- coding: utf-8 -*-
"""Лексеры редактора: LTX/DLTX, XML и Lua (.script).

Цвета берутся только из :mod:`ui.theme` (PALETTE). QSS не управляет
стилями Scintilla, поэтому все цвета назначаются программно.

Конструкции LTX/DLTX и соответствующие им цвета:

    обычный текст                 syn_default
    ; комментарий                 syn_comment
    // комментарий                syn_comment
    #include "file.ltx"           syn_include + syn_value
    [section]                     syn_section
    ![section]                    syn_override   (DLTX: переопределение)
    !![section]                   syn_deleted    (DLTX: удаление секции)
    @[section]                    syn_safe       (DLTX: безопасное создание)
    [section]:parent1, parent2    syn_section + syn_parent
    [section]:!parent             syn_section + syn_deleted + syn_parent
    key = value                   syn_key + syn_assign + syn_value
    key = a, b, c                 syn_key + syn_assign + syn_list_value
    !key                          syn_deleted    (DLTX: удаление ключа)
    >key = value                  syn_list_add   (DLTX: добавление в CSV)
    <key = value                  syn_list_remove (DLTX: удаление из CSV)
    "строка"                      syn_value
    123 / 1.5 / 0xFF              syn_number
    true / false / nil / null     syn_constant
"""

from PyQt5.QtCore import QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont
from PyQt5.Qsci import (
    QsciLexerCustom,
    QsciLexerLua,
    QsciLexerXML,
    QsciScintilla,
)

try:  # запуск с корня проекта (src в sys.path)
    from ui.theme import PALETTE
except ImportError:  # запуск как пакета
    from .theme import PALETTE


# ---------------------------------------------------------------- LTX styles
# Единственный построчный разбор LTX/DLTX живёт в core. Здесь остаётся
# только сопоставление токенов со стилями Scintilla и палитрой.
from core.patching.ltx.syntax import (  # noqa: E402
    STYLE_ASSIGN,
    STYLE_COMMENT,
    STYLE_CONSTANT,
    STYLE_DEFAULT,
    STYLE_DIRECTIVE,
    STYLE_INCLUDE_PATH,
    STYLE_KEY,
    STYLE_KEY_DELETE,
    STYLE_KEY_INSERT,
    STYLE_KEY_REMOVE,
    STYLE_LIST_VALUE,
    STYLE_NUMBER,
    STYLE_PARENT,
    STYLE_PARENT_DELETE,
    STYLE_SECTION,
    STYLE_SECTION_DELETE,
    STYLE_SECTION_OVERRIDE,
    STYLE_SECTION_SAFE,
    STYLE_VALUE,
    tokenize_line,
)

_STYLE_COLORS = {
    STYLE_DEFAULT: PALETTE.syn_default,
    STYLE_COMMENT: PALETTE.syn_comment,
    STYLE_DIRECTIVE: PALETTE.syn_include,
    STYLE_SECTION: PALETTE.syn_section,
    STYLE_SECTION_OVERRIDE: PALETTE.syn_override,
    STYLE_SECTION_DELETE: PALETTE.syn_deleted,
    STYLE_SECTION_SAFE: PALETTE.syn_safe,
    STYLE_KEY: PALETTE.syn_key,
    STYLE_KEY_DELETE: PALETTE.syn_deleted,
    STYLE_KEY_INSERT: PALETTE.syn_list_add,
    STYLE_KEY_REMOVE: PALETTE.syn_list_remove,
    STYLE_ASSIGN: PALETTE.syn_assign,
    STYLE_VALUE: PALETTE.syn_value,
    STYLE_NUMBER: PALETTE.syn_number,
    STYLE_CONSTANT: PALETTE.syn_constant,
    STYLE_PARENT: PALETTE.syn_parent,
    STYLE_PARENT_DELETE: PALETTE.syn_deleted,
    STYLE_INCLUDE_PATH: PALETTE.syn_value,
    STYLE_LIST_VALUE: PALETTE.syn_list_value,
}

_BOLD_STYLES = {
    STYLE_DIRECTIVE,
    STYLE_SECTION,
    STYLE_SECTION_OVERRIDE,
    STYLE_SECTION_DELETE,
    STYLE_SECTION_SAFE,
}


class LtxLexer(QsciLexerCustom):
    """Кастомный лексер LTX/DLTX для QScintilla.

    Большие файлы размечаются порциями. После каждой порции управление
    возвращается циклу событий Qt, поэтому файл можно прокручивать и
    редактировать до окончания покраски.
    """

    progress = pyqtSignal(int, int)
    CHUNK_BYTES = 64 * 1024

    def __init__(self, parent=None):
        super().__init__(parent)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._continue_styling)
        self._next = None
        self._total = 0
        self._full_pass = False

        self.setDefaultColor(QColor(PALETTE.syn_default))
        self.setDefaultPaper(QColor(PALETTE.bg_main))
        self.setDefaultFont(self._font(STYLE_DEFAULT))

        for style, color in _STYLE_COLORS.items():
            self.setColor(QColor(color), style)
            self.setPaper(QColor(PALETTE.bg_main), style)
            self.setFont(self._font(style), style)

    # -------------------------------------------------- QsciLexerCustom API
    def language(self):
        return "LTX"

    def description(self, style):
        return {
            STYLE_DEFAULT: "Default",
            STYLE_COMMENT: "Comment",
            STYLE_DIRECTIVE: "Preprocessor",
            STYLE_SECTION: "Section",
            STYLE_SECTION_OVERRIDE: "DLTX override section",
            STYLE_SECTION_DELETE: "DLTX delete section",
            STYLE_SECTION_SAFE: "DLTX safe section",
            STYLE_KEY: "Key",
            STYLE_KEY_DELETE: "DLTX deleted key",
            STYLE_KEY_INSERT: "DLTX list insert",
            STYLE_KEY_REMOVE: "DLTX list remove",
            STYLE_ASSIGN: "Assignment",
            STYLE_VALUE: "Value",
            STYLE_NUMBER: "Number",
            STYLE_CONSTANT: "Constant",
            STYLE_PARENT: "Parent",
            STYLE_PARENT_DELETE: "DLTX removed parent",
            STYLE_INCLUDE_PATH: "Include path",
            STYLE_LIST_VALUE: "List value",
        }.get(style, "")

    def defaultColor(self, style):
        return QColor(_STYLE_COLORS.get(style, PALETTE.syn_default))

    def defaultPaper(self, style):
        return QColor(PALETTE.bg_main)

    def defaultFont(self, style):
        return self._font(style)

    def stop(self):
        """Остановить отложенную покраску (перед сменой текста/лексера)."""
        self._timer.stop()
        self._next = None

    def styleText(self, start, end):
        editor = self.editor()
        if editor is None:
            return

        total = int(
            editor.SendScintilla(QsciScintilla.SCI_GETLENGTH)
        )
        start = max(0, int(start))
        end = total if end < 0 else min(int(end), total)
        if end <= start:
            return

        self._timer.stop()
        self._next = None
        self._total = total
        self._full_pass = start <= 0 and end >= total

        self._style_chunk(editor, start, end)

    def _continue_styling(self):
        if self._next is None:
            return

        editor = self.editor()
        if editor is None:
            return

        current_length = int(
            editor.SendScintilla(QsciScintilla.SCI_GETLENGTH)
        )
        if current_length != self._total:
            self.stop()
            return

        start, end = self._next
        self._next = None
        self._style_chunk(editor, start, end)

    def _style_chunk(self, editor, start, end):
        chunk_end = min(end, start + self.CHUNK_BYTES)
        raw = bytes(editor.bytes(start, chunk_end))

        # Не разрезаем строку посередине, если это возможно.
        if chunk_end < end:
            last_newline = raw.rfind(b"\n")
            if last_newline >= 0:
                raw = raw[:last_newline + 1]
                chunk_end = start + len(raw)

        if raw.endswith(b"\x00"):
            raw = raw[:-1]
            chunk_end = max(start, chunk_end - 1)

        text = raw.decode("utf-8", errors="replace")
        if not text:
            self._next = None
            if self._full_pass and self._total:
                self.progress.emit(self._total, self._total)
            return

        self.startStyling(start)

        tokens = []
        offset = 0
        for line in text.splitlines(keepends=True):
            for token_start, token_end, style in tokenize_line(line):
                tokens.append(
                    (offset + token_start, offset + token_end, style)
                )
            offset += len(line)

        cursor = 0
        for token_start, token_end, style in tokens:
            if token_start > cursor:
                gap = text[cursor:token_start]
                self.setStyling(len(gap.encode("utf-8")), STYLE_DEFAULT)
            token = text[token_start:token_end]
            self.setStyling(len(token.encode("utf-8")), style)
            cursor = token_end

        if cursor < len(text):
            tail = text[cursor:]
            self.setStyling(len(tail.encode("utf-8")), STYLE_DEFAULT)

        if self._total:
            self.progress.emit(min(chunk_end, self._total), self._total)
            editor.update()

        if chunk_end < end:
            self._next = (chunk_end, end)
            self._timer.start(0)
        else:
            self._next = None

    # -------------------------------------------------- служебное
    @staticmethod
    def _font(style):
        font = QFont(PALETTE.editor_font_family, PALETTE.editor_font_size_pt)
        if style in _BOLD_STYLES:
            font.setBold(True)
        return font


# ---------------------------------------------------------------- настройка
def _base_lexer(lexer):
    """Общие настройки встроенного лексера: шрифт, фон, дефолтный цвет."""
    font = QFont(PALETTE.editor_font_family, PALETTE.editor_font_size_pt)
    paper = QColor(PALETTE.bg_main)
    fg = QColor(PALETTE.syn_default)

    lexer.setDefaultFont(font)
    lexer.setDefaultColor(fg)
    lexer.setDefaultPaper(paper)

    # Перебить штатные per-style дефолты лексера (например, тёмные
    # цвета в QsciLexerXML/Lua, рассчитанные на светлый фон).
    lexer.setFont(font)
    lexer.setColor(fg)
    lexer.setPaper(paper)
    return lexer


def _configure_xml(lexer):
    _base_lexer(lexer)
    lexer.setColor(QColor(PALETTE.syn_section), QsciLexerXML.Tag)
    lexer.setColor(QColor(PALETTE.syn_section), QsciLexerXML.XMLTagEnd)
    lexer.setColor(QColor(PALETTE.syn_key), QsciLexerXML.Attribute)
    lexer.setColor(
        QColor(PALETTE.syn_value),
        QsciLexerXML.HTMLDoubleQuotedString,
    )
    lexer.setColor(
        QColor(PALETTE.syn_value),
        QsciLexerXML.HTMLSingleQuotedString,
    )
    lexer.setColor(QColor(PALETTE.syn_comment), QsciLexerXML.HTMLComment)
    lexer.setColor(QColor(PALETTE.syn_comment), QsciLexerXML.CDATA)
    lexer.setColor(QColor(PALETTE.syn_number), QsciLexerXML.HTMLNumber)
    lexer.setColor(QColor(PALETTE.syn_constant), QsciLexerXML.Entity)
    lexer.setColor(QColor(PALETTE.syn_directive), QsciLexerXML.SGMLCommand)
    return lexer


def _configure_lua(lexer):
    _base_lexer(lexer)

    lexer.setColor(QColor(PALETTE.syn_comment), QsciLexerLua.Comment)
    lexer.setColor(QColor(PALETTE.syn_comment), QsciLexerLua.LineComment)
    lexer.setColor(QColor(PALETTE.syn_number), QsciLexerLua.Number)
    lexer.setColor(QColor(PALETTE.syn_directive), QsciLexerLua.Keyword)
    lexer.setColor(QColor(PALETTE.syn_directive), QsciLexerLua.KeywordSet5)
    lexer.setColor(QColor(PALETTE.syn_directive), QsciLexerLua.KeywordSet6)
    lexer.setColor(QColor(PALETTE.syn_directive), QsciLexerLua.KeywordSet7)
    lexer.setColor(QColor(PALETTE.syn_directive), QsciLexerLua.KeywordSet8)
    lexer.setColor(QColor(PALETTE.syn_value), QsciLexerLua.String)
    lexer.setColor(QColor(PALETTE.syn_value), QsciLexerLua.LiteralString)
    lexer.setColor(QColor(PALETTE.syn_value), QsciLexerLua.Character)
    lexer.setColor(QColor(PALETTE.syn_deleted), QsciLexerLua.UnclosedString)
    lexer.setColor(QColor(PALETTE.syn_assign), QsciLexerLua.Operator)
    lexer.setColor(QColor(PALETTE.syn_key), QsciLexerLua.Identifier)
    lexer.setColor(QColor(PALETTE.syn_directive), QsciLexerLua.Preprocessor)
    lexer.setColor(QColor(PALETTE.syn_constant), QsciLexerLua.Label)
    lexer.setColor(QColor(PALETTE.syn_section), QsciLexerLua.BasicFunctions)
    lexer.setColor(
        QColor(PALETTE.syn_section),
        QsciLexerLua.StringTableMathsFunctions,
    )
    lexer.setColor(
        QColor(PALETTE.syn_section),
        QsciLexerLua.CoroutinesIOSystemFacilities,
    )
    return lexer


def create_lexer(path):
    """Создать и настроить лексер по расширению файла.

    .ltx                — собственный LTX/DLTX-лексер;
    .xml                — QsciLexerXML;
    .script / .lua      — QsciLexerLua;
    остальное / None    — без подсветки.
    """
    if path is None:
        return None

    suffix = str(path).lower().rsplit(".", 1)
    suffix = "." + suffix[-1] if len(suffix) == 2 else ""

    if suffix == ".ltx":
        return LtxLexer()
    if suffix == ".xml":
        return _configure_xml(QsciLexerXML())
    if suffix in (".script", ".lua"):
        return _configure_lua(QsciLexerLua())
    return None
