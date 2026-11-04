# -*- coding: utf-8 -*-
"""Лексеры редактора: LTX/DLTX, XML и Lua (.script).

Цвета берутся только из :mod:`ui.theme` (PALETTE). QSS не управляет
стилями Scintilla, поэтому все цвета назначаются программно.

Конструкции LTX/DLTX и соответствующие им цвета:

    обычный текст                 syn_default
    ; комментарий                 syn_comment
    // комментарий                syn_comment
    #include "file.ltx"           syn_directive + syn_value
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

import re

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
STYLE_DEFAULT = 0
STYLE_COMMENT = 1
STYLE_DIRECTIVE = 2
STYLE_SECTION = 3
STYLE_SECTION_OVERRIDE = 4
STYLE_SECTION_DELETE = 5
STYLE_SECTION_SAFE = 6
STYLE_KEY = 7
STYLE_KEY_DELETE = 8
STYLE_KEY_INSERT = 9
STYLE_KEY_REMOVE = 10
STYLE_ASSIGN = 11
STYLE_VALUE = 12
STYLE_NUMBER = 13
STYLE_CONSTANT = 14
STYLE_PARENT = 15
STYLE_PARENT_DELETE = 16
STYLE_INCLUDE_PATH = 17
STYLE_LIST_VALUE = 18

_STYLE_COLORS = {
    STYLE_DEFAULT: PALETTE.syn_default,
    STYLE_COMMENT: PALETTE.syn_comment,
    STYLE_DIRECTIVE: PALETTE.syn_directive,
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

_RE_DIRECTIVE = re.compile(r"#\s*[A-Za-z_][\w-]*")
_RE_NUMBER = re.compile(
    r"[+-]?(?:0[xX][0-9A-Fa-f]+|(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)"
)
_CONSTANTS = {"true", "false", "nil", "null"}


def _comment_index(line, start):
    """Индекс начала комментария (; или //) вне кавычек, либо -1."""
    in_quote = False
    i = start
    n = len(line)
    while i < n:
        ch = line[i]
        if ch == '"':
            in_quote = not in_quote
        elif not in_quote:
            if ch == ";":
                return i
            if ch == "/" and i + 1 < n and line[i + 1] == "/":
                return i
        i += 1
    return -1


def _find_unquoted(line, char, start, end):
    """Найти символ вне кавычек."""
    in_quote = False
    i = start
    while i < end:
        ch = line[i]
        if ch == '"':
            in_quote = not in_quote
        elif ch == char and not in_quote:
            return i
        i += 1
    return -1


def _has_unquoted_comma(line, start, end):
    """True, если в диапазоне есть запятая вне кавычек (значение-список)."""
    in_quote = False
    i = start
    while i < end:
        ch = line[i]
        if ch == '"':
            in_quote = not in_quote
        elif ch == "," and not in_quote:
            return True
        i += 1
    return False


def _tokenize_value(line, start, end, tokens, list_mode=False):
    """Разметить значение: строки, числа, константы, обычные значения."""
    i = start
    while i < end:
        ch = line[i]

        if ch.isspace():
            i += 1
            continue

        if ch == ",":
            tokens.append((i, i + 1, STYLE_ASSIGN))
            i += 1
            continue

        if ch == '"':
            j = i + 1
            while j < end:
                if line[j] == '"':
                    j += 1
                    break
                j += 1
            tokens.append(
                (i, j, STYLE_LIST_VALUE if list_mode else STYLE_VALUE)
            )
            i = j
            continue

        j = i
        while j < end and not line[j].isspace() and line[j] != ",":
            j += 1
        word = line[i:j]
        low = word.lower()

        if low in _CONSTANTS:
            tokens.append((i, j, STYLE_CONSTANT))
        elif _RE_NUMBER.fullmatch(word):
            tokens.append((i, j, STYLE_NUMBER))
        else:
            tokens.append(
                (i, j, STYLE_LIST_VALUE if list_mode else STYLE_VALUE)
            )
        i = j


def _tokenize_key(line, start, end, tokens):
    """Разметить ключ с учётом DLTX-операторов !, >, <."""
    while start < end and line[start].isspace():
        start += 1
    while end > start and line[end - 1].isspace():
        end -= 1
    if start >= end:
        return

    prefix = line[start]
    if prefix == "!":
        tokens.append((start, end, STYLE_KEY_DELETE))
    elif prefix == ">":
        tokens.append((start, start + 1, STYLE_KEY_INSERT))
        tokens.append((start + 1, end, STYLE_KEY))
    elif prefix == "<":
        tokens.append((start, start + 1, STYLE_KEY_REMOVE))
        tokens.append((start + 1, end, STYLE_KEY))
    else:
        tokens.append((start, end, STYLE_KEY))


def _tokenize_parents(line, start, end, tokens):
    """Разметить список родителей после ``]:`` (с DLTX-удалением !parent)."""
    i = start
    while i < end:
        if line[i] == ",":
            tokens.append((i, i + 1, STYLE_ASSIGN))
            i += 1
            continue

        j = i
        while j < end and line[j] != ",":
            j += 1

        p_start, p_end = i, j
        while p_start < p_end and line[p_start].isspace():
            p_start += 1
        while p_end > p_start and line[p_end - 1].isspace():
            p_end -= 1

        if p_start < p_end:
            if line[p_start] == "!":
                tokens.append((p_start, p_start + 1, STYLE_PARENT_DELETE))
                if p_start + 1 < p_end:
                    tokens.append((p_start + 1, p_end, STYLE_PARENT))
            else:
                tokens.append((p_start, p_end, STYLE_PARENT))
        i = j


def _tokenize_directive(line, start, end, tokens):
    """Разметить #include и его аргументы."""
    match = _RE_DIRECTIVE.match(line, start)
    if match is None:
        tokens.append((start, end, STYLE_DIRECTIVE))
        return

    tokens.append((match.start(), match.end(), STYLE_DIRECTIVE))
    i = match.end()
    while i < end:
        ch = line[i]
        if ch.isspace():
            i += 1
            continue
        if ch == '"':
            j = i + 1
            while j < end:
                if line[j] == '"':
                    j += 1
                    break
                j += 1
            tokens.append((i, j, STYLE_INCLUDE_PATH))
            i = j
            continue

        j = i
        while j < end and not line[j].isspace() and line[j] != '"':
            j += 1
        tokens.append((i, j, STYLE_VALUE))
        i = j


def tokenize_line(line):
    """Вернуть список (start, end, style) для одной строки LTX/DLTX.

    Функция не зависит от QScintilla и используется тестами.
    """
    body = line.rstrip("\r\n")
    tokens = []
    n = len(body)

    indent = 0
    while indent < n and body[indent] in " \t":
        indent += 1
    if indent >= n:
        return tokens

    if body.startswith(";", indent) or body.startswith("//", indent):
        tokens.append((indent, n, STYLE_COMMENT))
        return tokens

    if body[indent] == "#":
        comment = _comment_index(body, indent)
        end = comment if comment != -1 else n
        _tokenize_directive(body, indent, end, tokens)
        if comment != -1:
            tokens.append((comment, n, STYLE_COMMENT))
        return tokens

    # Заголовок секции: [sec], ![sec], !![sec], @[sec].
    section_style = None
    if body.startswith("!![", indent):
        section_style = STYLE_SECTION_DELETE
    elif body.startswith("![", indent):
        section_style = STYLE_SECTION_OVERRIDE
    elif body.startswith("@[", indent):
        section_style = STYLE_SECTION_SAFE
    elif body.startswith("[", indent):
        section_style = STYLE_SECTION

    if section_style is not None:
        close = body.find("]", indent)
        if close == -1:
            tokens.append((indent, n, section_style))
            return tokens

        tokens.append((indent, close + 1, section_style))

        rest = close + 1
        comment = _comment_index(body, rest)
        end = comment if comment != -1 else n
        if rest < end and body[rest] == ":":
            tokens.append((rest, rest + 1, STYLE_ASSIGN))
            _tokenize_parents(body, rest + 1, end, tokens)
            rest = end

        # Хвост после заголовка/родителей — комментарий.
        if rest < n:
            tail = body[rest:]
            stripped = tail.lstrip()
            if stripped.startswith(";") or stripped.startswith("//"):
                comment_start = rest + (len(tail) - len(stripped))
                tokens.append((comment_start, n, STYLE_COMMENT))
        return tokens

    # key = value (или просто key / !key).
    comment = _comment_index(body, indent)
    end = comment if comment != -1 else n
    eq = _find_unquoted(body, "=", indent, end)

    if eq == -1:
        _tokenize_key(body, indent, end, tokens)
    else:
        _tokenize_key(body, indent, eq, tokens)
        tokens.append((eq, eq + 1, STYLE_ASSIGN))
        list_mode = _has_unquoted_comma(body, eq + 1, end)
        _tokenize_value(body, eq + 1, end, tokens, list_mode=list_mode)

    if comment != -1:
        tokens.append((comment, n, STYLE_COMMENT))
    return tokens


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

        if self._full_pass and self._total:
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
