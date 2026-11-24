# -*- coding: utf-8 -*-
"""Центр операций платформы.

Кнопка в строке состояния показывает число активных операций; по клику
открывается немодальный список с прогрессом и отменой. Это не отдельный
загрузочный экран модуля и не блокировка рабочей среды.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class OperationCenterDialog(QDialog):
    """Немодальный список операций."""

    def __init__(self, parent=None, on_cancel=None, on_clear=None):
        super().__init__(parent)
        self.setWindowTitle("Операции")
        self.setModal(False)
        self.resize(760, 340)
        self._on_cancel = on_cancel
        self._on_clear = on_clear
        self._snapshots = ()
        self._signature = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.summary = QLabel("Операций нет")
        layout.addWidget(self.summary)

        self.table = QTableWidget(0, 5, self)
        self.table.setHorizontalHeaderLabels(
            ("Операция", "Владелец", "Состояние", "Прогресс", "Сообщение")
        )
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        self.cancel_button = QPushButton("Отменить выбранную")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_selected)
        buttons.addWidget(self.cancel_button)

        self.clear_button = QPushButton("Очистить завершённые")
        self.clear_button.clicked.connect(self._clear_finished)
        buttons.addWidget(self.clear_button)
        buttons.addStretch(1)

        close_button = QPushButton("Закрыть")
        close_button.clicked.connect(self.close)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

    def set_snapshots(self, snapshots):
        snapshots = tuple(snapshots or ())
        signature = tuple(
            (
                item.key,
                item.state,
                item.done,
                item.total,
                item.message,
                item.error,
                item.duplicate_count,
            )
            for item in snapshots
        )
        if signature == self._signature:
            return
        self._signature = signature
        self._snapshots = snapshots
        self._rebuild()

    def _rebuild(self):
        selected = self._selected_key()
        active = sum(1 for item in self._snapshots if item.is_active)
        total = len(self._snapshots)
        if active:
            self.summary.setText(
                "Активных операций: %d; всего в списке: %d" % (active, total)
            )
        elif total:
            self.summary.setText("Активных операций нет")
        else:
            self.summary.setText("Операций нет")

        self.table.setRowCount(len(self._snapshots))
        for row, item in enumerate(self._snapshots):
            title = item.display_title
            if item.duplicate_count:
                title += " (повторных запросов: %d)" % item.duplicate_count
            message = item.error or item.message
            values = (
                title,
                item.owner or "<platform>",
                item.state_label,
                self._progress_text(item),
                message,
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if column == 0:
                    cell.setData(Qt.UserRole, item.key)
                self.table.setItem(row, column, cell)

        if selected is not None:
            for row in range(self.table.rowCount()):
                cell = self.table.item(row, 0)
                if cell is not None and cell.data(Qt.UserRole) == selected:
                    self.table.selectRow(row)
                    break
        self._update_buttons()

    @staticmethod
    def _progress_text(item):
        if item.total > 0:
            return "%d/%d" % (item.done, item.total)
        return "—"

    def _selected_key(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        cell = self.table.item(row, 0)
        return None if cell is None else cell.data(Qt.UserRole)

    def _update_buttons(self):
        key = self._selected_key()
        snapshot = next(
            (item for item in self._snapshots if item.key == key),
            None,
        )
        self.cancel_button.setEnabled(
            snapshot is not None and snapshot.is_active
        )
        self.clear_button.setEnabled(
            any(item.is_finished for item in self._snapshots)
        )

    def _cancel_selected(self):
        key = self._selected_key()
        if key and callable(self._on_cancel):
            self._on_cancel(key)

    def _clear_finished(self):
        if callable(self._on_clear):
            self._on_clear()


class OperationCenter(QWidget):
    """Компактный индикатор в строке состояния."""

    def __init__(self, registry, parent=None, on_cancel=None, on_clear=None):
        super().__init__(parent)
        self._registry = registry
        self._on_cancel = on_cancel
        self._on_clear = on_clear
        self._snapshots = ()
        self._dialog = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.button = QToolButton(self)
        self.button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.button.setText("Операции")
        self.button.clicked.connect(self.open)
        layout.addWidget(self.button)
        self.setVisible(False)

    def refresh(self):
        snapshots = self._registry.snapshots()
        self._snapshots = snapshots
        active = [item for item in snapshots if item.is_active]
        if active:
            self.button.setText("Операции: %d" % len(active))
            self.button.setToolTip("Открыть центр операций")
            self.setVisible(True)
        elif snapshots:
            self.button.setText("Операции: готово")
            self.button.setToolTip("Открыть центр операций")
            self.setVisible(True)
        else:
            self.setVisible(False)
        if self._dialog is not None and self._dialog.isVisible():
            self._dialog.set_snapshots(snapshots)

    def open(self):
        if self._dialog is None:
            self._dialog = OperationCenterDialog(
                self,
                on_cancel=self._on_cancel,
                on_clear=self._on_clear,
            )
        self._dialog.set_snapshots(self._snapshots)
        self._dialog.show()
        self._dialog.raise_()
        self._dialog.activateWindow()

    def closeEvent(self, event):
        if self._dialog is not None:
            self._dialog.close()
            self._dialog = None
        super().closeEvent(event)
