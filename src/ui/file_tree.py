# -*- coding: utf-8 -*-
"""Дерево файлов — проекция базового дерева ядра (LtxForest).

UI-слой НЕ меняет базовое дерево: он строит презентационную проекцию.
Плоские списки детей каждого узла группируются здесь в виртуальные папки
по сегментам относительного пути — отдельно для каждого родителя (корня).
"""

from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import QStyle, QTreeWidget, QTreeWidgetItem

DRIVE_KEY = "::drive::"
FOLDER_PREFIX = "::folder::"


def _segments(forest, path):
    """Сегменты относительного пути узла (нормализовано к '/')."""
    rel = forest.rel_paths.get(path, str(path)).replace("\\", "/")
    return tuple(s for s in rel.split("/") if s)


class SortableTreeWidgetItem(QTreeWidgetItem):
    """Числовая сортировка колонок 1 и 2; вторичный ключ — имя (FR-12.4)."""

    def __lt__(self, other):
        tree = self.treeWidget()
        if tree is not None and tree.isSortingEnabled():
            col = tree.sortColumn()
            if col in (1, 2):
                try:
                    a, b = int(self.text(col)), int(other.text(col))
                    if a != b:
                        return a < b
                except ValueError:
                    pass
                return self.text(0).lower() < other.text(0).lower()
        return super().__lt__(other)


class FileTreeWidget(QTreeWidget):
    file_opened = pyqtSignal(str)

    HL_COLOR = QColor("#4f4f00")

    def __init__(self, sort_fn=None, parent=None):
        super().__init__(parent)

        self.setHeaderLabels(["Имя", "Файлов в дереве", "Прямых детей"])
        self.setColumnWidth(0, 520)

        self.setSortingEnabled(True)
        self.sortByColumn(1, Qt.DescendingOrder)

        self._sort = sort_fn or (
            lambda forest, nodes, col="path", reverse=False: sorted(nodes)
        )

        self.forest = None
        self._drive_item = None
        self._path_to_item = {}     # str(Path) реального узла -> item
        self._folder_items = {}     # key виртуальной папки -> item
        self._child_index = {}      # (parent_key, имя) -> item
        self._group_children = {}   # key виртуальной папки -> список entries
        self._populated = set()
        self._highlight = set()

        self.itemDoubleClicked.connect(self._on_double_clicked)
        self.itemExpanded.connect(self._on_expanded)

    # -------------------------------------------------- группировка по путям
    def _build_folder(self, parent_key, name, members, depth):
        """Виртуальная папка. members — пути, проходящие через неё;
        depth — индекс сегмента имени в их путях. Возвращает entry папки."""
        sub_buckets = {}
        sub_files = []
        total = 0

        for p in members:
            total += self.forest.subtree_counts.get(p, 1)
            segs = _segments(self.forest, p)
            if len(segs) == depth + 1:
                sub_files.append(p)
            else:
                sub_buckets.setdefault(segs[depth], []).append(p)

        folder_key = FOLDER_PREFIX + parent_key + ">::" + name

        sub_entries = [
            self._build_folder(folder_key, sub_name, sub_members, depth + 1)
            for sub_name, sub_members in sub_buckets.items()
        ]
        sub_entries += [("file", p) for p in sub_files]

        self._group_children[folder_key] = sub_entries
        return ("folder", name, folder_key, total, len(sub_entries))

    def _group_entries(self, parent_key, kids):
        """Группирует плоский список узлов в entries верхнего уровня родителя."""
        buckets = {}
        files = []

        for p in kids:
            segs = _segments(self.forest, p)
            if not segs:
                continue
            if len(segs) == 1:
                files.append(p)
            else:
                buckets.setdefault(segs[0], []).append(p)

        entries = [
            self._build_folder(parent_key, name, members, 1)
            for name, members in buckets.items()
        ]
        entries += [("file", p) for p in files]
        return entries

    # -------------------------------------------------- загрузка проекции
    def load_forest(self, forest):
        self.forest = forest
        self.clear()
        self._path_to_item.clear()
        self._folder_items.clear()
        self._child_index.clear()
        self._group_children.clear()
        self._populated.clear()

        self._drive_item = SortableTreeWidgetItem(
            self,
            [
                "%s (LTX VFS)" % Path(forest.scan_dir).name,
                str(forest.total_files),
                "",
            ],
        )
        self._drive_item.setData(0, Qt.UserRole, DRIVE_KEY)
        self._drive_item.setIcon(0, self.style().standardIcon(QStyle.SP_DirIcon))
        self._drive_item.addChild(SortableTreeWidgetItem())
        self._drive_item.setExpanded(True)  # ленивая группировка корней

        if forest.system_root:
            item = self._path_to_item.get(str(Path(forest.system_root)))
            if item is not None:
                item.setExpanded(True)

    # -------------------------------------------------- наполнение узлов
    def _populate(self, parent_item, parent_key, entries):
        dir_icon = self.style().standardIcon(QStyle.SP_DirIcon)

        for e in entries:
            if e[0] == "folder":
                _, name, fkey, total, direct = e
                item = SortableTreeWidgetItem(
                    parent_item, [name, str(total), str(direct)]
                )
                item.setData(0, Qt.UserRole, fkey)
                item.setIcon(0, dir_icon)
                self._folder_items[fkey] = item
                self._child_index[(parent_key, name)] = item
                item.addChild(SortableTreeWidgetItem())
            else:
                self._add_node(parent_item, e[1], parent_key)

    def _add_node(self, parent_item, path, parent_key=None):
        key = str(path)

        if key in self._path_to_item:
            return self._path_to_item[key]

        f = self.forest
        kids = f.display_children.get(path, [])
        segs = _segments(f, path)
        name = segs[-1] if segs else f.rel_paths.get(path, str(path))

        # «Прямых детей» с учётом группировки: сколько элементов увидит пользователь
        if kids:
            top_names = {_segments(f, c)[0] for c in kids if _segments(f, c)}
            direct = len(top_names)
        else:
            direct = 0

        item = SortableTreeWidgetItem(
            parent_item,
            [name, str(f.subtree_counts.get(path, 1)), str(direct)],
        )
        item.setData(0, Qt.UserRole, key)
        item.setIcon(0, self.style().standardIcon(QStyle.SP_FileIcon))
        self._path_to_item[key] = item
        if parent_key is not None:
            self._child_index[(parent_key, name)] = item

        if key in self._highlight:
            item.setBackground(0, self.HL_COLOR)

        if kids:
            item.addChild(SortableTreeWidgetItem())

        return item

    def _on_expanded(self, item):
        key = item.data(0, Qt.UserRole)

        if not key or self.forest is None or key in self._populated:
            return

        if key.startswith(FOLDER_PREFIX):
            entries = self._group_children.get(key, [])
        elif key == DRIVE_KEY:
            entries = self._group_entries(DRIVE_KEY, list(self.forest.roots))
            self._drive_item.setText(2, str(len(entries)))
        else:
            kids = self.forest.display_children.get(Path(key), [])
            if not kids:
                return
            entries = self._group_entries(key, kids)

        if not entries:
            return

        item.takeChildren()
        self._populate(item, key, entries)
        self._populated.add(key)

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
    def _walk_down(self, item, key, segs):
        """Спуск сквозь виртуальные папки по сегментам пути."""
        cur_item, cur_key = item, key

        for seg in segs:
            if not cur_item.isExpanded():
                cur_item.setExpanded(True)
            child = self._child_index.get((cur_key, seg))
            if child is None:
                return None
            cur_item = child
            cur_key = child.data(0, Qt.UserRole)

        return cur_item

    def reveal_path(self, path):
        """Раскрыть цепочку реальных родителей и виртуальных папок, выделить узел."""
        if self.forest is None or self._drive_item is None:
            return

        path = Path(path)

        chain = []
        cur = path
        while cur is not None:
            chain.append(cur)
            cur = self.forest.node_parent.get(cur)
        chain.reverse()  # от корня к цели

        item, key = self._drive_item, DRIVE_KEY

        for node in chain:
            item = self._walk_down(item, key, _segments(self.forest, node))
            if item is None:
                return
            key = item.data(0, Qt.UserRole)
            if node != path and not item.isExpanded():
                item.setExpanded(True)

        self.setCurrentItem(item)
        self.scrollToItem(item)

    # -------------------------------------------------- действия
    def _on_double_clicked(self, item, column):
        key = item.data(0, Qt.UserRole)
        if key and not key.startswith("::"):
            self.file_opened.emit(key)

    def path_at(self, pos):
        item = self.itemAt(pos)
        if item is None:
            return None
        key = item.data(0, Qt.UserRole)
        if not key or key.startswith("::"):  # драйв и виртуальные папки
            return None
        return Path(key)

    # -------------------------------------------------- сортировка
    def sort_by_files_desc(self):
        self.setSortingEnabled(True)
        self.sortByColumn(1, Qt.DescendingOrder)