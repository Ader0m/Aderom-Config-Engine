# -*- coding: utf-8 -*-
"""Дерево файлов — проекция базового дерева ядра (LtxForest)."""

from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import QTreeWidget, QTreeWidgetItem


class FileTreeWidget(QTreeWidget):
    file_opened = pyqtSignal(str)

    HL_COLOR = QColor("#4f4f00")

    def __init__(self, sort_fn=None, parent=None):
        super().__init__(parent)

        self.setHeaderLabels(["Имя", "Файлов в дереве", "Прямых детей"])
        self.setColumnWidth(0, 520)

        self._sort = sort_fn or (
            lambda forest, nodes, col="path", reverse=False: sorted(nodes)
        )

        self.forest = None
        self._path_to_item = {}
        self._populated = set()
        self._highlight = set()

        self.itemDoubleClicked.connect(self._on_double_clicked)
        self.itemExpanded.connect(self._on_expanded)

    # -------------------------------------------------- загрузка проекции
    def load_forest(self, forest):
        self.forest = forest
        self.clear()
        self._path_to_item.clear()
        self._populated.clear()

        drive = QTreeWidgetItem(
            self,
            [
                "%s (LTX VFS)" % Path(forest.scan_dir).name,
                str(forest.total_files),
                str(len(forest.roots)),
            ],
        )
        drive.setExpanded(True)

        for p in self._sort(forest, forest.roots):
            self._add_node(drive, p)

        if forest.system_root:
            item = self._path_to_item.get(str(Path(forest.system_root)))
            if item is not None:
                item.setExpanded(True)

    # -------------------------------------------------- узлы
    def _add_node(self, parent_item, path):
        key = str(path)

        if key in self._path_to_item:
            return self._path_to_item[key]

        f = self.forest
        kids = f.display_children.get(path, [])

        item = QTreeWidgetItem(
            parent_item,
            [
                f.rel_paths.get(path, str(path)),
                str(f.subtree_counts.get(path, 1)),
                str(len(kids)),
            ],
        )
        item.setData(0, Qt.UserRole, key)
        self._path_to_item[key] = item

        if key in self._highlight:
            item.setBackground(0, self.HL_COLOR)

        if kids:
            item.addChild(QTreeWidgetItem())

        return item

    def _on_expanded(self, item):
        key = item.data(0, Qt.UserRole)

        if not key or self.forest is None:
            return

        path = Path(key)

        if path in self._populated:
            return

        kids = self.forest.display_children.get(path, [])
        if not kids:
            return

        item.takeChildren()

        for c in self._sort(self.forest, kids):
            self._add_node(item, c)

        self._populated.add(path)

    # -------------------------------------------------- поиск: подсветка
    def highlight(self, paths):
        self._highlight = {str(p) for p in paths}

        for key, item in self._path_to_item.items():
            if key in self._highlight:
                item.setBackground(0, self.HL_COLOR)
            else:
                item.setBackground(0, QBrush())

    def clear_highlight(self):
        self._highlight = set()

        for item in self._path_to_item.values():
            item.setBackground(0, QBrush())

    # -------------------------------------------------- поиск: показать узел
    def reveal_path(self, path):
        """Раскрыть цепочку родителей до узла и выделить его."""
        if self.forest is None:
            return

        path = Path(path)

        chain = []
        cur = path
        while cur is not None:
            chain.append(cur)
            cur = self.forest.node_parent.get(cur)

        # раскрываем сверху вниз; раскрытие родителя создаёт детей
        for p in reversed(chain):
            item = self._path_to_item.get(str(p))
            if item is not None and not item.isExpanded():
                item.setExpanded(True)

        item = self._path_to_item.get(str(path))
        if item is not None:
            self.setCurrentItem(item)
            self.scrollToItem(item)

    # -------------------------------------------------- действия
    def _on_double_clicked(self, item, column):
        key = item.data(0, Qt.UserRole)
        if key:
            self.file_opened.emit(key)

    def path_at(self, pos):
        item = self.itemAt(pos)
        if item is None:
            return None
        key = item.data(0, Qt.UserRole)
        return Path(key) if key else None