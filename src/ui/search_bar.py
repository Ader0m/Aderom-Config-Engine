# -*- coding: utf-8 -*-
"""Панель поиска по имени файла в дереве (платформенный виджет)."""

from PyQt5.QtCore import QEvent, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)


class SearchBar(QWidget):
    """
    Строка поиска над деревом.

    Сигналы:
        matches_changed(list)   — изменился набор совпадений (для подсветки);
        current_changed(object) — текущее совпадение (показать в дереве);
        cleared()               — поиск сброшен.
    """

    matches_changed = pyqtSignal(list)
    current_changed = pyqtSignal(object)
    cleared = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self._search_fn = None
        self._matches = []
        self._index = -1

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        layout.addWidget(QLabel("Поиск:"))

        self.entry = QLineEdit()
        self.entry.setPlaceholderText("имя файла .ltx …")
        self.entry.setClearButtonEnabled(True)
        layout.addWidget(self.entry, 1)

        self.status = QLabel("0/0")
        layout.addWidget(self.status)

        self.btn_prev = QPushButton("↑")
        self.btn_prev.setFixedWidth(24)
        self.btn_prev.setToolTip("Предыдущее совпадение (Shift+Enter)")
        self.btn_prev.clicked.connect(self.prev)
        layout.addWidget(self.btn_prev)

        self.btn_next = QPushButton("↓")
        self.btn_next.setFixedWidth(24)
        self.btn_next.setToolTip("Следующее совпадение (Enter)")
        self.btn_next.clicked.connect(self.next)
        layout.addWidget(self.btn_next)

        self.btn_close = QPushButton("×")
        self.btn_close.setFixedWidth(24)
        self.btn_close.setToolTip("Закрыть поиск (Esc)")
        self.btn_close.clicked.connect(self.hide_and_clear)
        layout.addWidget(self.btn_close)

        self.entry.textChanged.connect(self._on_text_changed)
        self.entry.installEventFilter(self)

        self.hide()

    # -------------------------------------------------- API для окна
    def set_search_fn(self, fn):
        """fn(query: str) -> list[path]"""
        self._search_fn = fn

    def show_and_focus(self):
        self.show()
        self.entry.setFocus()
        self.entry.selectAll()

    def hide_and_clear(self):
        self.entry.blockSignals(True)
        self.entry.clear()
        self.entry.blockSignals(False)

        self._matches = []
        self._index = -1
        self.status.setText("0/0")
        self.hide()
        self.cleared.emit()

    def refresh(self):
        """Пересчитать совпадения (например, после построения нового дерева)."""
        self._on_text_changed(self.entry.text())

    # -------------------------------------------------- клавиши
    def eventFilter(self, obj, ev):
        if obj is self.entry and ev.type() == QEvent.KeyPress:
            if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
                if ev.modifiers() & Qt.ShiftModifier:
                    self.prev()
                else:
                    self.next()
                return True

            if ev.key() == Qt.Key_Escape:
                self.hide_and_clear()
                return True

        return super().eventFilter(obj, ev)

    # -------------------------------------------------- логика
    def _on_text_changed(self, text):
        q = (text or "").strip().lower()

        if not q or self._search_fn is None:
            self._matches = []
            self._index = -1
            self.status.setText("0/0")
            self.matches_changed.emit([])
            self.cleared.emit()
            return

        self._matches = list(self._search_fn(q) or [])
        self._index = 0 if self._matches else -1
        self._emit_state()

    def next(self):
        if not self._matches:
            return
        self._index = (self._index + 1) % len(self._matches)
        self._emit_state()

    def prev(self):
        if not self._matches:
            return
        self._index = (self._index - 1) % len(self._matches)
        self._emit_state()

    def _emit_state(self):
        total = len(self._matches)

        if self._index >= 0:
            self.status.setText("%d/%d" % (self._index + 1, total))
        else:
            self.status.setText("0/%d" % total)

        self.matches_changed.emit(list(self._matches))

        if self._index >= 0:
            self.current_changed.emit(self._matches[self._index])