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

from PyQt5.QtCore import (
    Qt,
    pyqtSignal,
    QAbstractListModel,
    QModelIndex,
    QObject,
    QPoint,
    QEvent,
    QTimer,
)
from PyQt5.QtGui import QColor, QFont, QKeySequence
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QShortcut,
    QVBoxLayout,
    QWidget,
    QAbstractItemView,
    QSplitter,
    QFrame, 
    QStyledItemDelegate,
    QListView,
    
)

from PyQt5.Qsci import QsciScintilla

try:  # запуск с корня проекта (src в sys.path)
    from ui.theme import PALETTE
    from ui.lexers import create_lexer
    from ui.debounce import Debounce
except ImportError:  # запуск как пакета
    from .theme import PALETTE
    from .lexers import create_lexer
    from .debounce import Debounce


class _LexerScheduler(QObject):
    """Откладывает покраску файла и обновляет статус прогресса."""

    status = pyqtSignal(str)

    def __init__(self, editor, set_lexer, parent=None):
        super().__init__(parent)
        self._editor = editor
        self._set_lexer = set_lexer
        self._path = None
        self._generation = 0
        self._last_percent = -1
        self._max_percent = -1

    def schedule(self, path):
        self._generation += 1
        self._path = str(path) if path is not None else None
        self._last_percent = -1
        self._max_percent = -1

        if self._path is None:
            return

        generation = self._generation
        QTimer.singleShot(0, lambda: self._apply(generation))

    def stop(self):
        self._generation += 1
        self._path = None
        self._last_percent = -1
        self._max_percent = -1
        self._set_lexer(None)

    def _apply(self, generation):
        if generation != self._generation or self._path is None:
            return

        path = self._path
        name = Path(path).name
        self.status.emit("Покраска: %s…" % name)

        try:
            lexer = create_lexer(path)
        except Exception as exc:
            self.status.emit("Покраска: %s — ошибка: %s" % (name, exc))
            return

        if lexer is None:
            self.status.emit("Покраска: %s — готово" % name)
            return

        progress = getattr(lexer, "progress", None)
        if progress is not None:
            progress.connect(
                lambda done, total, p=path: self._on_progress(p, done, total)
            )

        try:
            self._set_lexer(lexer)
        except Exception as exc:
            self.status.emit("Покраска: %s — ошибка: %s" % (name, exc))
            return

        length = getattr(self._editor, "length", None)
        empty = not length() if callable(length) else False

        if progress is None or empty:
            self.status.emit("Покраска: %s — готово" % name)

    def _on_progress(self, path, done, total):
        if path != self._path:
            return

        percent = int(done * 100 / total) if total else 100

        # Scintilla может запрашивать видимые диапазоны при прокрутке.
        # Прогресс не должен откатываться назад, а «готово» — повторяться.
        if percent < self._max_percent:
            return
        if percent == 100 and self._max_percent == 100:
            return
        if percent < 100 and percent - self._last_percent < 2:
            return

        self._last_percent = percent
        self._max_percent = max(self._max_percent, percent)
        name = Path(path).name

        if percent >= 100:
            self.status.emit("Покраска: %s — готово" % name)
        else:
            self.status.emit("Покраска: %s — %d%%" % (name, percent))


class EditorFindBar(QWidget):
    """Панель поиска ВНУТРИ файла (поисковые API QScintilla)."""

    def __init__(self, scintilla, parent=None):
        super().__init__(parent)

        self.scintilla = scintilla
        self._debounce = Debounce(self._apply_pending_search, parent=self)

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
        self._debounce.cancel()
        self.hide()
        self.scintilla.setFocus(Qt.OtherFocusReason)

    def hideEvent(self, event):
        self._debounce.cancel()
        super().hideEvent(event)

    def _on_text_changed(self, text):
        if not text:
            self._debounce.cancel()
            self.status.setText("")
            return

        self._debounce.schedule()

    def _apply_pending_search(self):
        if self.entry.text():
            self._find(forward=True)
        else:
            self.status.setText("")

    def find_next(self):
        self._debounce.cancel()
        self._find(forward=True)

    def find_prev(self):
        self._debounce.cancel()
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
    content_changed = pyqtSignal(str)
    save_requested = pyqtSignal()
    dirty_changed = pyqtSignal(bool)
    lexer_status = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.current_file = None
        # QScintilla не забирает владение Python-объектом лексера,
        # поэтому сильную ссылку нужно держать здесь.
        self._lexer = None
        self._loading = False
        self._editable = False
        self._readonly_content = True
        self._baseline_text = ""
        self._dirty = False

        self._init_ui()
        self._init_shortcuts()
        self._lexer_scheduler = _LexerScheduler(
            self.editor,
            self._set_lexer,
            self,
        )
        self._lexer_scheduler.status.connect(self.lexer_status.emit)

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

        self.notice_label = QLabel()
        self.notice_label.setWordWrap(True)
        self.notice_label.setStyleSheet(
            "padding: 6px 8px; background-color: {bg}; color: {fg};".format(
                bg=PALETTE.bg_panel, fg=PALETTE.syn_comment
            )
        )
        self.notice_label.hide()
        layout.addWidget(self.notice_label)

        self.editor = QsciScintilla()
        self._setup_editor()
        self.editor.textChanged.connect(self._on_text_changed)

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
        QShortcut(
            QKeySequence("Ctrl+S"),
            self,
            context=Qt.WidgetWithChildrenShortcut,
            activated=self._request_save,
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
        # До создания проекта файлы доступны только для чтения.
        self.editor.setReadOnly(True)

    def _set_lexer(self, lexer):
        """Установить лексер, удержав его Python-ссылку.

        QsciScintilla хранит только C++-указатель и не становится
        владельцем Python-обёртки. Без ``self._lexer`` сборщик мусора
        удаляет лексер сразу после выхода из метода, и подсветка пропадает.
        """
        old = self._lexer
        if old is not None and old is not lexer:
            stop = getattr(old, "stop", None)
            if callable(stop):
                stop()

        self._lexer = lexer
        self.editor.setLexer(lexer)
        self.editor.setTabWidth(4)
        # setLexer(None) сбрасывает цвета полей Scintilla на светлые.
        self._restore_editor_colors()

    def _restore_editor_colors(self):
        """Вернуть цвета полей/фона после смены лексера."""
        font = self._editor_font()
        self.editor.setMarginsFont(font)
        self.editor.setMarginsBackgroundColor(QColor(PALETTE.ed_margin_bg))
        self.editor.setMarginsForegroundColor(QColor(PALETTE.ed_margin_fg))
        self.editor.setColor(QColor(PALETTE.syn_default))
        self.editor.setPaper(QColor(PALETTE.bg_main))
        self.editor.setCaretForegroundColor(QColor(PALETTE.ed_caret))
        self.editor.setCaretLineBackgroundColor(QColor(PALETTE.ed_caret_line))

    # -------------------------------------------------- публичные действия
    def show_find(self):
        self.find_bar.show_and_focus()

    def undo(self):
        self.editor.undo()

    def redo(self):
        self.editor.redo()

    # -------------------------------------------------- содержимое
    @staticmethod
    def _read_text(path: Path) -> str:
        # XML в X-Ray/Anomaly всегда windows-1251, без автоопределения.
        if path.suffix.lower() == ".xml":
            raw = path.read_bytes()
            if raw.startswith(b"\xef\xbb\xbf"):
                raw = raw[3:]
            return raw.decode("windows-1251", errors="replace")

        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("windows-1251", errors="replace")

    def _show_content(self, text, label, source_path=None, readonly=False,
                      syntax=True, notice=None):
        """Единая точка отображения содержимого в редакторе.

        ``source_path`` задаёт файл, из которого взято содержимое: по нему
        выбирается лексер, и он же становится ``current_file``. И
        ``open_file``, и ``show_text`` делегируют сюда, поэтому оба пути
        получают одинаковую подсветку синтаксиса. ``syntax=False``
        отключает лексер. ``notice`` показывает дисклеймер над файлом,
        не добавляя его в текст и не сдвигая номера строк.
        """
        text = str(text or "")
        self._loading = True
        self._readonly_content = bool(readonly)

        # Старый лексер может начать разбор нового текста — сначала
        # останавливаем его, затем показываем содержимое без покраски.
        self._lexer_scheduler.stop()

        self.editor.setReadOnly(False)
        self.editor.setText(text)
        self.editor.setReadOnly(
            self._readonly_content or not self._editable
        )

        self._baseline_text = text
        self._set_dirty(False)

        self.current_file = str(source_path) if source_path is not None else None
        self.file_label.setText(str(label))
        notice_text = str(notice or "").strip()
        self.notice_label.setText(notice_text)
        self.notice_label.setVisible(bool(notice_text))
        self._loading = False

        # Покраска — отдельная отложенная задача. Файл уже доступен
        # для просмотра, прокрутки и редактирования.
        if syntax:
            self._lexer_scheduler.schedule(source_path)
        else:
            self._set_lexer(None)

        # Фокус в редактор: без этого внутренняя карта клавиш Scintilla
        # (undo/redo/копирование/поиск) "не работает" для пользователя.
        self.editor.setFocus(Qt.OtherFocusReason)

    def open_file(self, file_path, text=None):
        path = Path(file_path)

        if text is None:
            if not path.is_file():
                return
            text = self._read_text(path)

        self._show_content(
            text,
            str(path),
            source_path=path,
            readonly=False,
        )

    # -------------------------------------------------- проект
    def set_editable(self, editable):
        """Разрешить или запретить редактирование текущего содержимого."""
        self._editable = bool(editable)
        self.editor.setReadOnly(
            self._readonly_content or not self._editable
        )

    @property
    def is_editable(self):
        return self._editable and not self._readonly_content

    @property
    def is_dirty(self):
        return self._dirty

    def text(self) -> str:
        return self.editor.text()

    def mark_clean(self, text=None):
        """Зафиксировать текущий текст как сохранённый в проекте."""
        self._baseline_text = self.text() if text is None else str(text)
        self._set_dirty(self.text() != self._baseline_text)

    def _set_dirty(self, dirty):
        dirty = bool(dirty)
        if dirty == self._dirty:
            return
        self._dirty = dirty
        self.dirty_changed.emit(dirty)

    def _on_text_changed(self):
        if self._loading:
            return

        text = self.text()
        self.content_changed.emit(text)
        self._set_dirty(text != self._baseline_text)

    def _request_save(self):
        if self.is_editable:
            self.save_requested.emit()

    def goto_line(self, line_number):
        """Перейти к указанной строке (1-based)."""
        if line_number < 1:
            return

        line_index = line_number - 1
        self.editor.setCursorPosition(line_index, 0)
        self.editor.ensureLineVisible(line_index)
        self.editor.setFocus(Qt.OtherFocusReason)

    def find_first(self, text):
        """Перейти к первому вхождению ``text`` и выделить его."""
        if not text:
            return False
        self.editor.setCursorPosition(0, 0)
        found = self.editor.findFirst(text, False, False, False, True)
        if found:
            self.editor.setFocus(Qt.OtherFocusReason)
        return found

    def show_text(self, text, label, readonly=True, source_path=None,
                  line_number=None, syntax=True, notice=None):
        path = Path(source_path) if source_path is not None else None
        self._show_content(
            text,
            label,
            source_path=path,
            readonly=readonly,
            syntax=syntax,
            notice=notice,
        )
        if line_number is not None:
            self.goto_line(int(line_number))


class ResolvedFindBar(QWidget):
    """Панель поиска внутри ResolvedView."""

    def __init__(self, scintilla, parent=None):
        super().__init__(parent)
        self.scintilla = scintilla
        self._debounce = Debounce(self._apply_pending_search, parent=self)

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
        btn_close.setToolTip("Закрыть (Esc)")
        btn_close.clicked.connect(self.hide)
        layout.addWidget(btn_close)

        self.entry.textChanged.connect(self._on_text_changed)
        self.hide()

    def _on_text_changed(self, text):
        if not text:
            self._debounce.cancel()
            self.status.setText("")
            return

        self._debounce.schedule()

    def _apply_pending_search(self):
        if self.entry.text():
            self._search(forward=True)
        else:
            self.status.setText("")

    def show_and_focus(self):
        self.show()
        self.entry.setFocus()
        self.entry.selectAll()

    def hideEvent(self, event):
        self._debounce.cancel()
        super().hideEvent(event)

    def _search(self, forward=True):
        text = self.entry.text()
        if not text:
            self.status.setText("")
            return
        ok = self.scintilla.findFirst(
            text, False, False, False, True, forward,
        )
        self.status.setText("" if ok else "не найдено")

    def find_next(self):
        self._debounce.cancel()
        self._search(forward=True)

    def find_prev(self):
        self._debounce.cancel()
        self._search(forward=False)


ORIGINAL_MARKER = "__ORIGINAL__"   # маркер из core/resolver_adapter.py


class SourceListModel(QAbstractListModel):
    """Ленивая модель панели источников.

    Держит ТОЛЬКО список владельцев строк. Никаких объектов на строку:
    Qt запрашивает data()/flags() только для видимых строк.
    """

    ORIGINAL_LABEL = "оригинал"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sources = []

    def set_sources(self, sources):
        self.beginResetModel()
        self._sources = list(sources)
        self.endResetModel()

    def source_at(self, row):
        if 0 <= row < len(self._sources):
            return self._sources[row]
        return None

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._sources)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        src = self._sources[index.row()]

        kind = getattr(src, "kind", None)
        if kind is not None:
            if kind == "original":
                pretty = self.ORIGINAL_LABEL
            else:
                pretty = getattr(src, "display", "") or ""
        elif isinstance(src, tuple):
            pretty = src[0]
        else:
            pretty = src

        if role == Qt.DisplayRole:
            if pretty is None:
                return ""
            if pretty == ORIGINAL_MARKER:
                return self.ORIGINAL_LABEL
            return pretty
        if role == Qt.ToolTipRole:
            if kind is not None:
                if kind in ("file", "patch"):
                    return "Двойной клик — открыть файл-источник"
                return None
            if not isinstance(src, tuple):
                return None
            return "Двойной клик — открыть файл-источник"
        return None

    def flags(self, index):
        base = super().flags(index)
        if not index.isValid():
            return Qt.NoItemFlags

        src = self._sources[index.row()]
        kind = getattr(src, "kind", None)
        if kind is not None:
            if kind in ("file", "patch") and getattr(src, "path", None):
                return base
            return base & ~Qt.ItemIsEnabled

        if not isinstance(src, tuple):
            return base & ~Qt.ItemIsEnabled
        return base


class SourceListView(QListView):
    """Панель источников поверх SourceListModel."""

    lineActivated = pyqtSignal(int)        # одиночный клик (row)
    lineDoubleActivated = pyqtSignal(int)  # двойной клик (row)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._model = SourceListModel(self)
        self.setModel(self._model)

        self.setUniformItemSizes(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setViewportMargins(0, 0, 0, 0)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSelectionMode(QAbstractItemView.SingleSelection)

        self.clicked.connect(lambda idx: self.lineActivated.emit(idx.row()))
        self.doubleClicked.connect(lambda idx: self.lineDoubleActivated.emit(idx.row()))

    # -------------------------------------------------- данные
    def set_sources(self, sources):
        self._model.set_sources(sources)

    def source_at(self, row):
        return self._model.source_at(row)

    # -------------------------------------------------- синхронизация
    def scroll_to_line(self, row):
        idx = self._model.index(row, 0)
        if idx.isValid():
            self.scrollTo(idx, QAbstractItemView.PositionAtTop)

    def first_visible_row(self):
        idx = self.indexAt(QPoint(0, 0))
        return idx.row() if idx.isValid() else -1

    def select_line(self, row):
        idx = self._model.index(row, 0)
        if idx.isValid():
            self.setCurrentIndex(idx)

class SourceItemDelegate(QStyledItemDelegate):
    """Держит высоту строки списка источников равной высоте строки редактора."""

    def __init__(self, line_height, parent=None):
        super().__init__(parent)
        self._line_height = int(line_height)

    def set_line_height(self, line_height):
        self._line_height = int(line_height)

    def sizeHint(self, option, index):
        hint = super().sizeHint(option, index)
        hint.setHeight(self._line_height)
        return hint

class ResolvedView(QWidget):
    """Резолв-вид: QScintilla слева + панель источников справа + поиск."""

    ORIGINAL = "__ORIGINAL__"
    ORIGINAL_LABEL = "оригинал"

    source_activated = pyqtSignal(object, str)  # (path, search_text)
    source_raw_activated = pyqtSignal(object, str)  # сырой файл (XML/script)
    diagnostic = pyqtSignal(str)
    content_changed = pyqtSignal(str)
    save_requested = pyqtSignal()
    dirty_changed = pyqtSignal(bool)
    lexer_status = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._root_path = None
        self._line_sources = []
        self._lexer = None
        self._syncing = False
        self._source_visible = True
        self._loading = False
        self._editable = False
        self._editing_supported = True
        self._baseline_text = ""
        self._dirty = False
        self._init_ui()
        self._init_shortcuts()
        self._lexer_scheduler = _LexerScheduler(
            self.editor,
            self._set_lexer,
            self,
        )
        self._lexer_scheduler.status.connect(self.lexer_status.emit)

    # -------------------------------------------------- UI
    def _init_ui(self):
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # --- верхняя полоса: заголовок файла + заголовок панели источников ---
        top_bar = QWidget()
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(0)

        self.file_label = QLabel("Резолв не загружен")
        self.file_label.setStyleSheet(
            "padding: 4px; background-color: {bg}; color: {fg};".format(
                bg=PALETTE.bg_titlebar, fg=PALETTE.text
            )
        )
        top_layout.addWidget(self.file_label, 1)

        # Заголовок панели источников сидит в ТОЙ ЖЕ полосе, что и
        # file_label, поэтому список начинается строго с первой строки
        # редактора и пиксельной рассинхронизации по вертикали нет.
        self.source_header = QLabel("Источник")
        self.source_header.setAlignment(Qt.AlignCenter)
        self.source_header.setStyleSheet(
            "padding: 4px; background-color: {bg}; color: {fg};".format(
                bg=PALETTE.bg_titlebar, fg=PALETTE.text
            )
        )
        top_layout.addWidget(self.source_header, 0)

        main_layout.addWidget(top_bar)

        # --- содержимое: редактор слева, панель источников справа ---
        self.splitter = QSplitter(Qt.Horizontal)

        self.editor = QsciScintilla()
        self._setup_editor()
        self.editor.textChanged.connect(self._on_text_changed)

        self.source_list = SourceListView()
        self.source_list.lineActivated.connect(self._on_source_activated)
        self.source_list.lineDoubleActivated.connect(self._on_source_double_clicked)
        self.source_list.setMinimumWidth(180)
        self.source_list.setMaximumWidth(600)

        # Шрифт и высота строки списка — 1:1 с редактором.
        font = QFont(PALETTE.editor_font_family, PALETTE.editor_font_size_pt)
        self.source_list.setFont(font)
        line_height = self.editor.SendScintilla(QsciScintilla.SCI_TEXTHEIGHT, 0)
        self._source_delegate = SourceItemDelegate(line_height)
        self.source_list.setItemDelegate(self._source_delegate)

        self.splitter.addWidget(self.editor)
        self.splitter.addWidget(self.source_list)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([900, 300])
        self.splitter.splitterMoved.connect(self._sync_header_width)

        main_layout.addWidget(self.splitter, 1)

        # --- поиск внутри файла ---
        self.find_bar = ResolvedFindBar(self.editor)
        main_layout.addWidget(self.find_bar)

        self.setLayout(main_layout)

        # --- синхронизация: скролл и курсор ---
        self.editor.verticalScrollBar().valueChanged.connect(self._on_editor_scroll)
        self.source_list.verticalScrollBar().valueChanged.connect(self._on_source_scroll)
        self.editor.cursorPositionChanged.connect(self._on_editor_cursor_changed)

    def _init_shortcuts(self):
        QShortcut(
            QKeySequence("Ctrl+F"),
            self,
            context=Qt.WidgetWithChildrenShortcut,
            activated=self._show_find,
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
        QShortcut(
            QKeySequence("Ctrl+S"),
            self,
            context=Qt.WidgetWithChildrenShortcut,
            activated=self._request_save,
        )

    def _setup_editor(self):
        font = QFont(PALETTE.editor_font_family, PALETTE.editor_font_size_pt)
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
        self.editor.setReadOnly(True)

        self._apply_lexer(None)

    def _set_lexer(self, lexer):
        """Установить лексер, удержав Python-ссылку и остановив старый."""
        old = self._lexer
        if old is not None and old is not lexer:
            stop = getattr(old, "stop", None)
            if callable(stop):
                stop()

        self._lexer = lexer
        self.editor.setLexer(lexer)
        self._restore_editor_colors()

    def _restore_editor_colors(self):
        font = QFont(PALETTE.editor_font_family, PALETTE.editor_font_size_pt)
        self.editor.setMarginsFont(font)
        self.editor.setMarginsBackgroundColor(QColor(PALETTE.ed_margin_bg))
        self.editor.setMarginsForegroundColor(QColor(PALETTE.ed_margin_fg))
        self.editor.setColor(QColor(PALETTE.syn_default))
        self.editor.setPaper(QColor(PALETTE.bg_main))
        self.editor.setCaretForegroundColor(QColor(PALETTE.ed_caret))
        self.editor.setCaretLineBackgroundColor(QColor(PALETTE.ed_caret_line))

    def _apply_lexer(self, path):
        """Совместимость: применить лексер сразу (без отложенного запуска)."""
        self._set_lexer(create_lexer(path))

    # -------------------------------------------------- публичные API
    @property
    def root_path(self):
        return self._root_path

    @property
    def line_sources(self):
        return tuple(self._line_sources)

    @property
    def baseline_text(self):
        return self._baseline_text

    @property
    def is_dirty(self):
        return self._dirty

    def text(self):
        return self.editor.text()

    def set_editing_supported(self, supported):
        """Разрешить или запретить правку этого вида в принципе."""
        self._editing_supported = bool(supported)
        if not self._editing_supported:
            self._editable = False
            self.editor.setReadOnly(True)

    def set_editable(self, editable):
        self._editable = bool(editable) and self._editing_supported
        self.editor.setReadOnly(not self._editable)

    def mark_clean(self, text=None):
        self._baseline_text = self.text() if text is None else str(text)
        self._set_dirty(self.text() != self._baseline_text)

    def set_dirty_state(self, dirty):
        """Установить признак изменений без пересчёта всего текста."""
        self._set_dirty(bool(dirty))

    def apply_line_edit(self, action, line_number, text=""):
        """Точечная правка строки без замены всего текста.

        Используется карточкой предмета: system.ltx может быть очень
        большим, и полный ``setText`` на каждую правку недопустим.
        """
        editor = self.editor
        try:
            line_number = int(line_number)
        except (TypeError, ValueError):
            return False

        line_count = editor.lines()
        old_loading = self._loading
        old_readonly = editor.isReadOnly()
        self._loading = True
        editor.setReadOnly(False)

        def content_length(line):
            raw = editor.text(line)
            length = editor.lineLength(line)
            if raw.endswith("\r\n"):
                return max(0, length - 2)
            if raw.endswith("\n") or raw.endswith("\r"):
                return max(0, length - 1)
            return max(0, length)

        try:
            if action == "replace":
                line = line_number - 1
                if line < 0 or line >= line_count:
                    return False
                editor.setSelection(
                    line, 0, line, content_length(line)
                )
                editor.replaceSelectedText(str(text))

            elif action == "insert":
                if line_number < 0 or line_number > line_count:
                    return False
                if line_number == 0:
                    editor.setCursorPosition(0, 0)
                    editor.insert(str(text) + "\n")
                else:
                    line = line_number - 1
                    editor.setCursorPosition(
                        line,
                        content_length(line),
                    )
                    editor.insert("\n" + str(text))

            elif action == "delete":
                line = line_number - 1
                if line < 0 or line >= line_count:
                    return False
                if line + 1 < line_count:
                    editor.setSelection(line, 0, line + 1, 0)
                elif line > 0:
                    editor.setSelection(
                        line - 1,
                        content_length(line - 1),
                        line,
                        content_length(line),
                    )
                else:
                    editor.setSelection(
                        0, 0, 0, content_length(0)
                    )
                editor.removeSelectedText()

            else:
                return False

            sources = list(self._line_sources)
            while len(sources) < line_count:
                sources.append(None)

            if action == "insert":
                index = min(max(line_number, 0), len(sources))
                sources.insert(index, None)
            elif action == "delete":
                index = line_number - 1
                if 0 <= index < len(sources):
                    del sources[index]

            self._line_sources = sources
            return True
        finally:
            editor.setReadOnly(old_readonly)
            self._loading = old_loading

    def _set_dirty(self, dirty):
        dirty = bool(dirty)
        if dirty == self._dirty:
            return
        self._dirty = dirty
        self.dirty_changed.emit(dirty)

    def _on_text_changed(self):
        if self._loading:
            return
        text = self.text()
        self.content_changed.emit(text)
        self._set_dirty(text != self._baseline_text)

    def _request_save(self):
        if self._editable:
            self.save_requested.emit()

    def set_sources_visible(self, visible):
        self._source_visible = bool(visible)
        self.source_header.setVisible(self._source_visible)
        self.source_list.setVisible(self._source_visible)
        if not self._source_visible:
            self.splitter.setSizes([1, 0])

    def set_content(self, text, line_sources, root_path, sources_visible=True):
        self._root_path = str(root_path)
        self._line_sources = list(line_sources)

        self.set_sources_visible(sources_visible)

        text = str(text or "")
        self._loading = True
        self._lexer_scheduler.stop()
        self.editor.setReadOnly(False)
        self.editor.setText(text)
        self.editor.setReadOnly(not self._editable)
        self._baseline_text = text
        self._set_dirty(False)
        self._loading = False

        # beginResetModel/endResetModel внутри set_sources полностью
        # заменяют данные панели; clear() не нужен (и у QListView его нет).
        self.source_list.set_sources(self._line_sources)

        rel = str(root_path)
        self.file_label.setText("RESOLVED: %s" % rel)
        self.editor.setCursorPosition(0, 0)

        # Покраска запускается после отображения текста.
        self._lexer_scheduler.schedule(root_path)

    def find_first(self, text):
        if not text:
            return False
        self.editor.setCursorPosition(0, 0)
        return self.editor.findFirst(text, False, False, False, True)

    def goto_line(self, line_number):
        """Перейти к строке (1-based) в XML-резолв-вью."""
        if line_number < 1:
            return
        line_index = line_number - 1
        self.editor.setCursorPosition(line_index, 0)
        self.editor.ensureLineVisible(line_index)
        self.editor.setFocus(Qt.OtherFocusReason)

    def _show_find(self):
        self.find_bar.show_and_focus()

    # -------------------------------------------------- синхронизация
    def _on_editor_scroll(self, _value):
        if self._syncing:
            return
        self._syncing = True
        try:
            self.source_list.scroll_to_line(self.editor.firstVisibleLine())
        finally:
            self._syncing = False

    def _on_source_scroll(self, _value):
        if self._syncing:
            return
        self._syncing = True
        try:
            row = self.source_list.first_visible_row()
            if row >= 0:
                self.editor.setFirstVisibleLine(row)
        finally:
            self._syncing = False

    def _on_editor_cursor_changed(self, line, _index):
        if self._syncing:
            return
        self._syncing = True
        try:
            self.source_list.select_line(line)
        finally:
            self._syncing = False

    def _on_source_activated(self, row):
        if 0 <= row < self.editor.lines():
            self.editor.setCursorPosition(row, 0)
            self.editor.ensureLineVisible(row)
            self.editor.setFocus(Qt.OtherFocusReason)

    def _on_source_double_clicked(self, row):
        src = self.source_list.source_at(row)
        kind = getattr(src, "kind", None)

        if kind is not None:
            pretty = "оригинал" if kind == "original" else (
                getattr(src, "display", "") or ""
            )
            full_path = getattr(src, "path", None)
            if kind not in ("file", "patch") or full_path is None:
                self.diagnostic.emit(
                    "Строка %d (%s): открывать нечего."
                    % (row + 1, pretty)
                )
                return

            if not (0 <= row < self.editor.lines()):
                self.diagnostic.emit(
                    "Строка %d: вне границ редактора (всего строк: %d)."
                    % (row + 1, self.editor.lines())
                )
                return

            line_text = self.editor.text(row).rstrip("\r\n")
            if getattr(src, "open_raw", False):
                self.source_raw_activated.emit(full_path, line_text)
            else:
                self.source_activated.emit(full_path, line_text)
            return

        if not isinstance(src, tuple):
            self.diagnostic.emit(
                "Строка %d: владельца нет (служебная строка или «оригинал») — открывать нечего."
                % (row + 1)
            )
            return

        pretty, full_path = src[0], src[1]
        if full_path is None:
            self.diagnostic.emit(
                "Строка %d (%s): в снапшоте дерева нет полного пути — "
                "файл отсутствует в forest.rel_paths, открытие невозможно."
                % (row + 1, pretty)
            )
            return

        if not (0 <= row < self.editor.lines()):
            self.diagnostic.emit(
                "Строка %d: вне границ редактора (всего строк: %d)."
                % (row + 1, self.editor.lines())
            )
            return

        line_text = self.editor.text(row).rstrip("\r\n")
        if not line_text.strip():
            self.diagnostic.emit(
                "Строка %d (%s): строка пустая — владелец откроется, "
                "но искать в нём нечего." % (row + 1, pretty)
            )

        if len(src) >= 3 and src[2] == "raw":
            self.source_raw_activated.emit(full_path, line_text)
            return

        self.source_activated.emit(full_path, line_text)

    def _sync_header_width(self, *_args):
        sizes = self.splitter.sizes()
        if len(sizes) == 2:
            self.source_header.setFixedWidth(max(0, sizes[1]))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_header_width()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_header_width()