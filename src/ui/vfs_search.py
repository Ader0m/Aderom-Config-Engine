# -*- coding: utf-8 -*-
"""Окно поиска по содержимому собранного VFS.

Окно не знает о ядре и не читает файлы: оно принимает запрос, отдаёт его
платформенному сценарию и показывает готовые совпадения. Поэтому поиск
можно выполнять в фоне, а окно остаётся отзывчивым.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)


class VfsSearchDialog(QDialog):
    """Немодальное окно результатов полнотекстового поиска по VFS."""

    def __init__(self, on_search, on_open, parent=None):
        super().__init__(parent)
        self._on_search = on_search
        self._on_open = on_open
        self._results_query = ""

        self.setWindowTitle("Поиск по VFS")
        self.resize(900, 600)

        layout = QVBoxLayout(self)

        query_row = QHBoxLayout()
        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText(
            "Строка для поиска по содержимому файлов VFS"
        )
        self.query_edit.returnPressed.connect(self._request_search)
        query_row.addWidget(self.query_edit, 1)

        self.case_check_box = QCheckBox("Учитывать регистр")
        query_row.addWidget(self.case_check_box)

        self.search_button = QPushButton("Найти")
        self.search_button.clicked.connect(self._request_search)
        query_row.addWidget(self.search_button)
        layout.addLayout(query_row)

        self.info_label = QLabel(
            "Введите строку и нажмите «Найти»."
        )
        layout.addWidget(self.info_label)

        self.tree = self._make_tree()
        layout.addWidget(self.tree, 1)

        button_row = QHBoxLayout()
        self.open_button = QPushButton("Открыть совпадение")
        self.open_button.setToolTip(
            "Открыть файл и перейти к первому вхождению строки."
        )
        self.open_button.clicked.connect(self._open_current)
        button_row.addWidget(self.open_button)

        close_button = QPushButton("Закрыть")
        close_button.clicked.connect(self.hide)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

    def _make_tree(self):
        tree = QTreeWidget()
        tree.setColumnCount(4)
        tree.setHeaderLabels(
            ["Файл", "Совпадений", "Строка", "Фрагмент"]
        )
        tree.setRootIsDecorated(False)
        tree.setAlternatingRowColors(True)
        tree.setUniformRowHeights(True)
        tree.setSelectionBehavior(QTreeWidget.SelectRows)
        tree.itemDoubleClicked.connect(self._on_item_double_clicked)

        header = tree.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        return tree

    # -------------------------------------------------- запрос
    def focus_query(self):
        self.raise_()
        self.activateWindow()
        self.query_edit.setFocus()
        self.query_edit.selectAll()

    def query(self) -> str:
        return self.query_edit.text().strip()

    def case_sensitive(self) -> bool:
        return self.case_check_box.isChecked()

    def _request_search(self):
        query = self.query()
        if not query:
            self.info_label.setText("Введите строку для поиска.")
            self.query_edit.setFocus()
            return
        self.clear_results()
        self.set_busy(query)
        self._on_search(query, self.case_sensitive())

    # -------------------------------------------------- состояние
    def set_busy(self, query, already_running=False):
        self._results_query = ""
        self.search_button.setEnabled(False)
        self.open_button.setEnabled(False)
        if already_running:
            self.info_label.setText(
                "Поиск уже выполняется: %s…" % query
            )
        else:
            self.info_label.setText("Поиск: %s…" % query)

    def show_results(self, result, elapsed=0.0):
        self.search_button.setEnabled(True)
        self.open_button.setEnabled(True)
        self._results_query = str(result.query or "")

        self.tree.clear()
        for match in result.matches:
            item = QTreeWidgetItem()
            item.setText(0, match.relative_path)
            item.setText(1, str(match.matches))
            item.setText(2, str(match.first_line))
            item.setText(3, match.snippet)
            item.setToolTip(0, str(match.path))
            item.setToolTip(3, match.snippet)
            item.setData(0, Qt.UserRole, match)
            self.tree.addTopLevelItem(item)

        if result.matches:
            self.info_label.setText(
                "Файлов проверено: %d; с совпадениями: %d; "
                "совпадений: %d; %.2f с."
                % (
                    result.files_scanned,
                    result.files_matched,
                    result.total_matches,
                    float(elapsed or 0.0),
                )
            )
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        else:
            self.info_label.setText(
                "Совпадений не найдено (проверено файлов: %d)."
                % result.files_scanned
            )

    def show_error(self, message):
        self.search_button.setEnabled(True)
        self.open_button.setEnabled(True)
        self.info_label.setText("Ошибка поиска: %s" % message)

    def show_cancelled(self):
        self.search_button.setEnabled(True)
        self.open_button.setEnabled(True)
        self.info_label.setText("Поиск отменён.")

    def clear_results(self):
        self.tree.clear()
        self._results_query = ""

    # -------------------------------------------------- открытие
    def _open_current(self):
        item = self.tree.currentItem()
        if item is None:
            return
        match = item.data(0, Qt.UserRole)
        if match is None:
            return
        self._on_open(match, self._results_query)

    def _on_item_double_clicked(self, item):
        self.tree.setCurrentItem(item)
        self._open_current()


__all__ = ["VfsSearchDialog"]
