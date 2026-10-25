# -*- coding: utf-8 -*-
"""Дерево файлов — проекция базового дерева ядра (LtxForest).

UI-слой НЕ меняет базовое дерево: он строит презентационную проекцию.
Плоские списки детей каждого узла группируются в виртуальные папки по
путям детей ОТНОСИТЕЛЬНО ДИРЕКТОРИИ РОДИТЕЛЯ:
- дети, лежащие рядом с родителем (DLTX-патчи mod_*.ltx), прилипают
  к нему напрямую, не создавая ложных цепочек папок;
- дети из подкаталогов родителя группируются в папки как в реальной ФС;
- дети из других мест сохраняют полный путь от корня скана.
"""

from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import QStyle, QTreeWidget, QTreeWidgetItem

DRIVE_KEY = "::drive::"
FOLDER_PREFIX = "::folder::"
SORT_ROLE = Qt.UserRole + 1

def _segments(forest, path):
    """Сегменты относительного пути узла (нормализовано к '/')."""
    rel = forest.rel_paths.get(path, str(path)).replace("\\", "/")
    return tuple(s for s in rel.split("/") if s)


def _child_segments(forest, parent_path, child_path):
    """Сегменты пути ребёнка, видимые из родителя.

    Совпадающая с директорией родителя часть пути ребёнка отрезается.
    Если ребёнок лежит вне директории родителя — остаётся полный путь
    от корня скана (честно показываем, что файл из другого места).
    """
    segs = _segments(forest, child_path)
    if parent_path is None:          # уровень драйва: корень скана
        return segs
    psegs = _segments(forest, parent_path)[:-1]   # директория родителя
    if psegs and segs[:len(psegs)] == psegs:
        return segs[len(psegs):]
    return segs


def patch_sort_name(name, parent_path):
        """Ключ сортировки имени mod файла.

        Для DLTX-патчей mod_<имя родителя>_* ключом является только "*" часть
        (сортировка по алфавиту хвоста). Для обычных файлов — полное имя, как раньше.
        Имя родителя берём из базового дерева (node_parent), а не из видимого
        родителя в UI, т.к. видимым родителем может быть виртуальная папка.
        """
        low = name.lower()
        if parent_path is not None:
            stem = parent_path.stem                      # parts.ltx -> parts
            for base in (stem.replace(".", "_"), stem):  # wpn.ak74.ltx -> wpn_ak74
                prefix = "mod_%s_" % base.lower()
                if low.startswith(prefix) and len(low) > len(prefix):
                    return low[len(prefix):]
        return low

class SortableTreeWidgetItem(QTreeWidgetItem):
    def _sort_key(self):
        v = self.data(0, SORT_ROLE)
        if v is None:
            v = self.text(0).lower()
        return v

    def _is_leaf(self):
        """Лист = нет прямых детей (колонка 2 == 0)."""
        try:
            return int(self.text(2)) == 0
        except ValueError:
            return False

    @staticmethod
    def _alpha_lt(tree, key_self, key_other):
        """Алфавит ВСЕГДА по возрастанию: компенсируем инверсию Descending,
        направление берём из заголовка (sortOrder() в PyQt5 может не быть)."""
        if tree.header().sortIndicatorOrder() == Qt.DescendingOrder:
            return key_self > key_other
        return key_self < key_other

    def __lt__(self, other):
        tree = self.treeWidget()
        if tree is not None and tree.isSortingEnabled():
            col = tree.sortColumn()

            if col in (1, 2):
                # Оба листа: числовой фильтр их не тасует (у обоих 1/0),
                # порядок всегда алфавитный по ключу сортировки.
                if self._is_leaf() and other._is_leaf():
                    return self._alpha_lt(tree, self._sort_key(), other._sort_key())

                try:
                    a, b = int(self.text(col)), int(other.text(col))
                    if a != b:
                        return a < b
                except ValueError:
                    pass

                # Равные числа у не-листов: алфавит как вторичный ключ
                return self._alpha_lt(tree, self._sort_key(), other._sort_key())

            if col == 0:
                # Сортировка по имени: направление задаёт клик по заголовку
                return self._sort_key() < other._sort_key()

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
        """Виртуальная папка. members — пары (segs, path), проходящие через
        неё; depth — индекс сегмента имени внутри этих segs."""
        sub_buckets = {}
        sub_files = []
        total = 0

        for segs, p in members:
            total += self.forest.subtree_counts.get(p, 1)
            if len(segs) == depth + 1:
                sub_files.append(p)
            else:
                sub_buckets.setdefault(segs[depth], []).append((segs, p))

        folder_key = FOLDER_PREFIX + parent_key + ">::" + name

        sub_entries = [
            self._build_folder(folder_key, sub_name, sub_members, depth + 1)
            for sub_name, sub_members in sub_buckets.items()
        ]
        sub_entries += [("file", p) for p in sub_files]

        self._group_children[folder_key] = sub_entries
        return ("folder", name, folder_key, total, len(sub_entries))

    def _group_entries(self, parent_key, parent_path, kids):
        """Группирует плоский список детей в entries уровня родителя.
        parent_path=None — уровень драйва (корень скана)."""
        buckets = {}
        files = []

        for p in kids:
            segs = _child_segments(self.forest, parent_path, p)
            if not segs:
                continue
            if len(segs) == 1:
                files.append(p)          # лежит рядом — лист без папок
            else:
                buckets.setdefault(segs[0], []).append((segs, p))

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

        # Массовая вставка: отключаем сортировку и перерисовку, иначе Qt
        # пересортировывает детей на КАЖДОМ addChild -> O(N^2) на 3k узлов.
        sorting_was = self.isSortingEnabled()
        self.setSortingEnabled(False)
        self.setUpdatesEnabled(False)
        try:
            for e in entries:
                if e[0] == "folder":
                    _, name, fkey, total, direct = e
                    item = SortableTreeWidgetItem([name, str(total), str(direct)])
                    item.setData(0, Qt.UserRole, fkey)
                    item.setData(0, SORT_ROLE, name.lower())
                    item.setIcon(0, dir_icon)
                    self._folder_items[fkey] = item
                    self._child_index[(parent_key, name)] = item
                    item.addChild(SortableTreeWidgetItem())
                    parent_item.addChild(item)
                else:
                    self._add_node(parent_item, e[1], parent_key)
        finally:
            self.setUpdatesEnabled(True)
            if sorting_was:
                self.setSortingEnabled(True)
            # ОДИН проход сортировки на всё наполненное событие
            self.sortByColumn(
                self.sortColumn(),
                self.header().sortIndicatorOrder(),
            )
    
    def _add_node(self, parent_item, path, parent_key=None):
        key = str(path)

        if key in self._path_to_item:
            return self._path_to_item[key]

        f = self.forest
        kids = f.display_children.get(path, [])
        segs = _segments(f, path)
        name = segs[-1] if segs else f.rel_paths.get(path, str(path))

        if kids:
            top_names = set()
            for c in kids:
                cs = _child_segments(f, path, c)
                if cs:
                    top_names.add(cs[0])
            direct = len(top_names)
        else:
            direct = 0

        item = SortableTreeWidgetItem(          # ← БЕЗ parent_item
            [name, str(f.subtree_counts.get(path, 1)), str(direct)],
        )
        item.setData(0, Qt.UserRole, key)

        parent_path = f.node_parent.get(path)
        item.setData(0, SORT_ROLE, patch_sort_name(name, parent_path))

        item.setIcon(0, self.style().standardIcon(QStyle.SP_FileIcon))
        self._path_to_item[key] = item
        if parent_key is not None:
            self._child_index[(parent_key, name)] = item

        if key in self._highlight:
            item.setBackground(0, self.HL_COLOR)

        if kids:
            item.addChild(SortableTreeWidgetItem())

        parent_item.addChild(item)   # ← вставка ПОСЛЕДНИМ шагом: сортировка видит готовый ключ
        return item

    def _on_expanded(self, item):
        key = item.data(0, Qt.UserRole)

        if not key or self.forest is None or key in self._populated:
            return

        if key.startswith(FOLDER_PREFIX):
            entries = self._group_children.get(key, [])
        elif key == DRIVE_KEY:
            entries = self._group_entries(DRIVE_KEY, None, list(self.forest.roots))
            self._drive_item.setText(2, str(len(entries)))
        else:
            node = Path(key)
            kids = self.forest.display_children.get(node, [])
            if not kids:
                return
            entries = self._group_entries(key, node, kids)

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

        for i, node in enumerate(chain):
            parent = chain[i - 1] if i > 0 else None
            segs = _child_segments(self.forest, parent, node)
            item = self._walk_down(item, key, segs)
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