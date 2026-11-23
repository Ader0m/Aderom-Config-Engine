# -*- coding: utf-8 -*-
"""Read-only карточка секции LTX.

Виджет только отображает прикладной DTO ``app.semantic.SectionCardData``.
Он не читает файлы, не строит модель и не выполняет запись.
"""

from __future__ import annotations

from dataclasses import replace

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from app.semantic import SectionCardData


class SectionCardDialog(QDialog):
    """Немодальное окно свойств секции и их источников."""

    source_requested = pyqtSignal(object, str)  # (path, search_text)
    PAGE_SIZE = 200

    def __init__(self, section_name, parent=None):
        super().__init__(parent)
        self._section_name = str(section_name or "")
        self._data = None
        self._properties = []
        self._filtered = []
        self._visible = []
        self._limit = self.PAGE_SIZE
        self._busy = False

        self.setWindowTitle("Карточка секции: %s" % self._section_name)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.resize(980, 620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.header = QLabel("Загрузка…")
        self.header.setWordWrap(True)
        layout.addWidget(self.header)

        filters = QHBoxLayout()
        self.key_filter = QLineEdit()
        self.key_filter.setPlaceholderText("Фильтр по ключу…")
        self.key_filter.textChanged.connect(self._apply_filter)
        filters.addWidget(self.key_filter, 1)

        self.source_filter = QLineEdit()
        self.source_filter.setPlaceholderText("Фильтр по источнику…")
        self.source_filter.textChanged.connect(self._apply_filter)
        filters.addWidget(self.source_filter, 1)
        layout.addLayout(filters)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            (
                "Ключ",
                "Значение",
                "Источник",
                "Операция",
                "Уверенность",
                "Цепочка",
            )
        )
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        self.table.itemSelectionChanged.connect(self._update_jump_button)
        layout.addWidget(self.table, 1)

        self.diagnostics = QLabel("")
        self.diagnostics.setWordWrap(True)
        layout.addWidget(self.diagnostics)

        actions = QHBoxLayout()
        self.jump_button = QPushButton("Перейти к объявлению")
        self.jump_button.setEnabled(False)
        self.jump_button.clicked.connect(self._emit_current_source)
        actions.addWidget(self.jump_button)

        self.more_button = QPushButton("Показать ещё")
        self.more_button.setVisible(False)
        self.more_button.clicked.connect(self._show_more)
        actions.addWidget(self.more_button)

        actions.addStretch(1)
        close_button = QPushButton("Закрыть")
        close_button.clicked.connect(self.close)
        actions.addWidget(close_button)
        layout.addLayout(actions)

        self._set_message("Загрузка семантической модели…")

    # -------------------------------------------------- состояние
    def _set_message(self, text):
        self.header.setText("%s\n%s" % (self._section_name, text))

    def set_busy(self, message="Загрузка семантической модели…"):
        self._busy = True
        self.table.setEnabled(False)
        self.key_filter.setEnabled(False)
        self.source_filter.setEnabled(False)
        self.jump_button.setEnabled(False)
        self.more_button.setVisible(False)
        self.diagnostics.setText("")
        self._set_message(message)

    def set_error(self, message):
        self._busy = False
        self.table.setEnabled(False)
        self.key_filter.setEnabled(False)
        self.source_filter.setEnabled(False)
        self.jump_button.setEnabled(False)
        self.more_button.setVisible(False)
        self.diagnostics.setText("")
        self._set_message("Ошибка: %s" % message)

    def set_missing(self, message="Секция не найдена в разрешённом корне."):
        self._busy = False
        self.table.setEnabled(False)
        self.key_filter.setEnabled(False)
        self.source_filter.setEnabled(False)
        self.jump_button.setEnabled(False)
        self.more_button.setVisible(False)
        self.diagnostics.setText("")
        self._set_message(message)

    def set_data(self, data: SectionCardData):
        if data is None:
            self.set_missing()
            return

        self._busy = False
        self._data = data
        self._properties = [
            self._bounded_property(prop) for prop in data.properties
        ]
        self._filtered = list(self._properties)
        self._limit = self.PAGE_SIZE
        self.table.setEnabled(True)
        self.key_filter.setEnabled(True)
        self.source_filter.setEnabled(True)
        self.key_filter.clear()
        self.source_filter.clear()

        parents = ", ".join(data.parents) if data.parents else "—"
        declaration = data.declaration.display if data.declaration else "—"
        self.header.setText(
            "[%s]\nТип: %s   Родители: %s   Заголовок: %s"
            % (data.name, data.kind or "—", parents, declaration)
        )
        self._render()
        self._set_diagnostics(data.diagnostics)
        self._update_jump_button()

    def _render(self):
        self._visible = list(self._filtered[:self._limit])
        self.table.setRowCount(len(self._visible))
        for row, prop in enumerate(self._visible):
            self._set_row(row, prop)

        remaining = len(self._filtered) - len(self._visible)
        self.more_button.setVisible(remaining > 0)
        if remaining > 0:
            self.more_button.setText("Показать ещё (%d)" % remaining)
        self._update_jump_button()

    def _show_more(self):
        self._limit += self.PAGE_SIZE
        self._render()

    @staticmethod
    def _short_value(value, limit=500):
        """Не копировать многомегабайтное значение целиком в ячейку."""
        text = "" if value is None else str(value)
        if len(text) <= limit:
            return text
        return "%s… (%d симв.)" % (text[:limit], len(text))

    def _bounded_property(self, prop):
        """Ограничить значение и в DTO, который держит виджет."""
        value = self._short_value(prop.value)
        if value == prop.value:
            return prop
        return replace(prop, value=value)

    def _set_row(self, row, prop):
        key_item = QTableWidgetItem(prop.key)
        value = self._short_value(prop.value)
        value_item = QTableWidgetItem(value)
        value_item.setToolTip(value)
        source_item = QTableWidgetItem(prop.source.display)
        if prop.source.file is not None:
            source_item.setToolTip(str(prop.source.file))
        chain = " -> ".join(item.display for item in prop.chain)
        chain_item = QTableWidgetItem(chain)
        chain_item.setToolTip(chain)

        self.table.setItem(row, 0, key_item)
        self.table.setItem(row, 1, value_item)
        self.table.setItem(row, 2, source_item)
        self.table.setItem(
            row,
            3,
            QTableWidgetItem(prop.source.operation),
        )
        self.table.setItem(
            row,
            4,
            QTableWidgetItem(prop.confidence),
        )
        self.table.setItem(row, 5, chain_item)

    def _set_diagnostics(self, diagnostics):
        if not diagnostics:
            self.diagnostics.setText("")
            self.diagnostics.setToolTip("")
            return
        text = "Диагностика: %d" % len(diagnostics)
        first = diagnostics[0].message
        self.diagnostics.setText("%s — %s" % (text, first))
        self.diagnostics.setToolTip(
            "\n".join(
                "[%s] %s" % (item.severity, item.message)
                for item in diagnostics
            )
        )

    # -------------------------------------------------- фильтр и переход
    def _apply_filter(self):
        if self._data is None:
            return
        key_text = self.key_filter.text().lower().strip()
        source_text = self.source_filter.text().lower().strip()
        visible = []
        for prop in self._properties:
            if key_text and key_text not in prop.key.lower():
                continue
            sources = [prop.source] + list(prop.chain)
            haystack = " ".join(
                " ".join(
                    (
                        source.display or "",
                        source.mod or "",
                        str(source.file or ""),
                    )
                )
                for source in sources
            ).lower()
            if source_text and source_text not in haystack:
                continue
            visible.append(prop)

        self._filtered = visible
        self._limit = self.PAGE_SIZE
        self._render()

    def _selected_property(self):
        row = self.table.currentRow()
        if 0 <= row < len(self._visible):
            return self._visible[row]
        return None

    def _update_jump_button(self):
        prop = self._selected_property()
        source = prop.source if prop is not None else (
            self._data.declaration if self._data is not None else None
        )
        self.jump_button.setEnabled(
            not self._busy
            and source is not None
            and source.file is not None
        )

    def _on_double_click(self, _row, _column):
        self._emit_current_source()

    def _emit_current_source(self):
        prop = self._selected_property()
        if prop is not None:
            source = prop.source
            search = prop.key
        elif self._data is not None:
            source = self._data.declaration
            search = self._data.name
        else:
            return
        if source is None or source.file is None:
            return
        self.source_requested.emit(source.file, search)


__all__ = ["SectionCardDialog"]
