# -*- coding: utf-8 -*-
"""Проводник .ogg-файлов доступной VFS.

Сканирование, группировка и фильтрация выполняются в фоновой задаче
(``ctx.tasks.run``). UI-поток получает уже готовое дерево и создаёт
элементы лениво: при первом раскрытии папки. Параметры комментариев
для файловых строк также читаются в фоне.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from PyQt5.QtCore import QPoint, Qt, QTimer
from PyQt5.QtGui import QBrush, QColor, QPalette
from PyQt5.QtWidgets import (
    QAbstractItemDelegate,
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sdk import (
    DEFAULT_SEARCH_DELAY_MS,
    ITEM_SECTION_MIME,
    decode_item_section,
    path_key as sdk_path_key,
)

from .ogg import (
    OggError,
    XraySoundComment,
    parse_xray_comment,
    read_comments_prefix,
)
from .sections import extract_sound_paths
from .sound_types import sound_type_short, sound_type_tooltip
from .sound_types_ui import SoundTypeField, SoundTypePicker

CHANGED_COLOR = "#6A9955"
NEW_COLOR = "#4FC1FF"

_NODE_ROLE = Qt.UserRole + 2
_LOADED_ROLE = Qt.UserRole + 3
_FIELDS_ROLE = Qt.UserRole + 4
_PARAMS_ROLE = Qt.UserRole + 5
_FIELDS_ERROR_ROLE = Qt.UserRole + 6
_SELECTED_ROLE = Qt.UserRole + 7

FIELD_HEADERS = (
    "Версия",
    "Min dist",
    "Max dist",
    "Base vol",
    "Тип звука",
    "Max AI dist",
)
SELECT_COLUMN = 0
NAME_COLUMN = 1
STATE_COLUMN = 2
FIELD_COLUMNS = tuple(range(3, 3 + len(FIELD_HEADERS)))

_HEADER_READ_LIMIT = 256 * 1024
_FULL_FALLBACK_LIMIT = 8 * 1024 * 1024


@dataclass(frozen=True)
class SoundEntry:
    path: Path
    is_new: bool = False
    changed: bool = False


@dataclass(frozen=True)
class SoundNode:
    """Готовый узел дерева, построенный вне UI-потока."""

    name: str
    path: Path
    is_folder: bool
    entry: Optional[SoundEntry] = None
    children: Tuple["SoundNode", ...] = ()


@dataclass(frozen=True)
class SoundTreeResult:
    entries: Tuple[SoundEntry, ...]
    nodes: Tuple[SoundNode, ...]


def scan_sounds(
    sounds_root: Path,
    extra: Iterable[SoundEntry] = (),
) -> List[SoundEntry]:
    """Собрать все .ogg из VFS и добавить новые черновики проекта."""
    found = {}

    root = Path(sounds_root)
    if root.is_dir():
        for dirpath, dirnames, filenames in os.walk(str(root)):
            dirnames.sort(key=str.lower)
            for filename in sorted(filenames, key=str.lower):
                if not filename.lower().endswith(".ogg"):
                    continue
                path = Path(dirpath) / filename
                key = sdk_path_key(path)
                found[key] = SoundEntry(path=path)

    for entry in extra:
        key = sdk_path_key(entry.path)
        found[key] = entry

    return sorted(found.values(), key=lambda item: str(item.path).lower())


def build_sound_tree(
    entries: Iterable[SoundEntry],
    sounds_root: Path,
) -> Tuple[SoundNode, ...]:
    """Сгруппировать плоский список файлов в дерево папок.

    Чистая функция: вызывается в фоновом потоке и не трогает Qt.
    """
    root = Path(sounds_root)
    buckets = {}

    for entry in entries:
        try:
            relative = Path(entry.path).relative_to(root)
        except ValueError:
            relative = Path(entry.path)

        parts = relative.parts
        if not parts:
            continue

        bucket = buckets
        for part in parts[:-1]:
            bucket = bucket.setdefault(part, {})
        bucket[parts[-1]] = entry

    def make(bucket, prefix):
        folders = []
        files = []

        for name, value in bucket.items():
            if isinstance(value, SoundEntry):
                files.append(
                    SoundNode(
                        name=name,
                        path=value.path,
                        is_folder=False,
                        entry=value,
                    )
                )
                continue

            folder_path = Path(root) / Path(*prefix) / name
            folders.append(
                SoundNode(
                    name=name,
                    path=folder_path,
                    is_folder=True,
                    children=make(value, prefix + (name,)),
                )
            )

        folders.sort(key=lambda item: item.name.lower())
        files.sort(key=lambda item: item.name.lower())
        return tuple(folders + files)

    return make(buckets, ())


def filter_sound_tree(
    nodes: Iterable[SoundNode],
    query: str,
) -> Tuple[SoundNode, ...]:
    """Оставить файлы, совпадающие с запросом, и их родителей."""
    query = str(query or "").strip().lower()
    if not query:
        return tuple(nodes)

    result = []
    for node in nodes:
        text = node.name.lower()
        path_text = str(node.path).lower()
        matched = query in text or query in path_text

        if not node.is_folder:
            if matched:
                result.append(node)
            continue

        if matched:
            # Совпала папка — показываем её содержимое целиком.
            result.append(node)
            continue

        children = filter_sound_tree(node.children, query)
        if children:
            result.append(replace(node, children=children))

    return tuple(result)


def iter_file_nodes(node: SoundNode):
    if not node.is_folder:
        yield node
        return
    for child in node.children:
        yield from iter_file_nodes(child)


def _entry_state(entry: SoundEntry) -> str:
    if entry.is_new:
        return "новый"
    if entry.changed:
        return "изменён"
    return ""


class _DropTreeWidget(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.drop_handler = None
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if (
            self.drop_handler is not None
            and self.drop_handler.can_accept_drop(event.mimeData())
        ):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if (
            self.drop_handler is not None
            and self.drop_handler.can_accept_drop(event.mimeData())
        ):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        if (
            self.drop_handler is not None
            and self.drop_handler.handle_item_drop(event.mimeData())
        ):
            event.acceptProposedAction()
            return
        super().dropEvent(event)


class _DropListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.drop_handler = None
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if (
            self.drop_handler is not None
            and self.drop_handler.can_accept_drop(event.mimeData())
        ):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if (
            self.drop_handler is not None
            and self.drop_handler.can_accept_drop(event.mimeData())
        ):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        if (
            self.drop_handler is not None
            and self.drop_handler.handle_item_drop(event.mimeData())
        ):
            event.acceptProposedAction()
            return
        super().dropEvent(event)


class SoundFieldDelegate(QStyledItemDelegate):
    """Редакторы для шести колонок параметров."""

    def __init__(self, window, tree):
        super().__init__(tree)
        self.window = window
        self.tree = tree

    def paint(self, painter, option, index):
        """Закрасить строку, отмеченную для мультиредактирования.

        Штатный QSS для ``QTreeWidget::item`` перекрывает background-роль,
        поэтому подсветку рисуем сами в делегате.
        """
        first = index.sibling(index.row(), 0)
        mark = first.data(_SELECTED_ROLE) if first.isValid() else None
        if mark:
            color = option.palette.highlight().color()
            if int(mark) == 2:
                color.setAlpha(110)
            painter.save()
            painter.fillRect(option.rect, color)
            painter.restore()
        super().paint(painter, option, index)

    def createEditor(self, parent, option, index):
        column = index.column()
        if column not in FIELD_COLUMNS:
            return None

        if column == FIELD_COLUMNS[0]:
            editor = QComboBox(parent)
            editor.addItem("1", 1)
            editor.addItem("2", 2)
            editor.addItem("3", 3)
            editor.activated.connect(
                lambda _index, field=editor: self._commit(field)
            )
            self._style_editor(editor)
            return editor

        if column == FIELD_COLUMNS[4]:
            editor = SoundTypeField(parent)
            editor.activated.connect(
                lambda field=editor: self._commit(field)
            )
            self._style_editor(editor)
            return editor

        editor = QDoubleSpinBox(parent)
        editor.setDecimals(3)
        editor.setRange(0.0, 1000000.0)
        editor.setSingleStep(0.1)
        editor.editingFinished.connect(
            lambda field=editor: self._commit(field)
        )
        self._style_editor(editor)
        return editor

    def _style_editor(self, editor):
        try:
            ui = self.window.ctx.ui
            background = ui.theme_color("bg_main", "#1E1F22")
            foreground = ui.theme_color("text", "#DCDCDC")
            border = ui.theme_color("border", "#3C3F41")
            selection = ui.theme_color("selection", "#2E436E")
            bright = ui.theme_color("text_bright", "#FFFFFF")
        except Exception:
            return

        editor.setStyleSheet(
            "QDoubleSpinBox, QComboBox {"
            " background-color: %s;"
            " color: %s;"
            " border: 1px solid %s;"
            " padding: 2px;"
            " selection-background-color: %s;"
            " selection-color: %s;"
            "}"
            "QComboBox QAbstractItemView {"
            " background-color: %s;"
            " color: %s;"
            " selection-background-color: %s;"
            " selection-color: %s;"
            "}"
            % (
                background,
                foreground,
                border,
                selection,
                bright,
                background,
                foreground,
                selection,
                bright,
            )
        )

    def _commit(self, editor):
        self.commitData.emit(editor)
        self.closeEditor.emit(
            editor,
            QAbstractItemDelegate.NoHint,
        )

    @staticmethod
    def _params_for(item):
        if item is None:
            return XraySoundComment()
        return item.data(0, _PARAMS_ROLE) or XraySoundComment()

    def setEditorData(self, editor, index):
        item = self.tree.itemFromIndex(index)
        params = self._params_for(item)
        column = index.column()

        if isinstance(editor, SoundTypeField):
            editor.set_value(params.game_type)
            return

        if isinstance(editor, QComboBox):
            position = editor.findData(int(params.version))
            editor.setCurrentIndex(position if position >= 0 else 0)
            return

        values = {
            FIELD_COLUMNS[1]: params.min_dist,
            FIELD_COLUMNS[2]: params.max_dist,
            FIELD_COLUMNS[3]: params.base_volume,
            FIELD_COLUMNS[5]: params.max_ai_dist,
        }
        editor.setValue(float(values.get(column, 0.0)))

    def setModelData(self, editor, model, index):
        item = self.tree.itemFromIndex(index)
        if item is None:
            return

        if isinstance(editor, SoundTypeField):
            value = editor.value()
        elif isinstance(editor, QComboBox):
            value = editor.currentData()
        else:
            value = editor.value()

        self.window.apply_cell_edit(item, index.column(), value)


class OggExplorerWindow(QWidget):
    """Окно-проводник. Открытие файла делегируется модулю."""

    def __init__(
        self,
        ctx,
        on_open,
        on_add,
        on_multi_edit=None,
        on_edit_field=None,
        on_open_section=None,
        parent=None,
    ):
        super().__init__(parent)
        self.ctx = ctx
        self._on_open = on_open
        self._on_add = on_add
        self._on_multi_edit = on_multi_edit
        self._on_edit_field = on_edit_field
        self._on_open_section = on_open_section

        self._pinned_keys = set()
        self.setAcceptDrops(True)

        self._game_root: Optional[Path] = None
        self._entries: Tuple[SoundEntry, ...] = ()
        self._tree_data: Tuple[SoundNode, ...] = ()

        self._scan_generation = 0
        self._filter_generation = 0

        self._fields_generation = 0
        self._fields_cache = {}
        self._fields_pending = set()

        self._selection_mode = False
        self._updating_selection = False
        self._selected_files = set()
        self._type_picker = None

        self.setWindowTitle("Редактор звуков (.ogg)")
        self.resize(1180, 620)
        self._apply_window_background()

        self._build_ui()

    def _apply_window_background(self):
        """Залить фон окна цветом основной темы, а не прозрачностью."""
        background = self.ctx.ui.theme_color("bg_main", "#2B2B2B")
        self.setObjectName("oggExplorerWindow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            "#oggExplorerWindow { background-color: %s; }" % background
        )

    # ------------------------------------------------------------ UI
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        top = QHBoxLayout()
        self.path_label = QLabel("Каталог звуков не определён")
        self.path_label.setWordWrap(True)
        top.addWidget(self.path_label, 1)

        self.refresh_button = QPushButton("Обновить")
        self.refresh_button.clicked.connect(self.reload)
        top.addWidget(self.refresh_button)

        self.add_button = QPushButton("Добавить .ogg...")
        self.add_button.clicked.connect(self._add_clicked)
        top.addWidget(self.add_button)

        self.section_button = QPushButton("Открыть секцию...")
        self.section_button.setToolTip(
            "Ищет секцию в итоговой версии system.ltx"
        )
        self.section_button.clicked.connect(self._open_section_clicked)
        top.addWidget(self.section_button)

        self.multi_button = QPushButton("Мультиредактирование")
        self.multi_button.setCheckable(True)
        self.multi_button.clicked.connect(self._toggle_selection_mode)
        top.addWidget(self.multi_button)
        layout.addLayout(top)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Фильтр по имени или пути…")
        self.filter_edit.textChanged.connect(self._schedule_filter)
        layout.addWidget(self.filter_edit)

        selection_row = QHBoxLayout()
        self.selection_label = QLabel("")
        self.selection_label.setStyleSheet("color: palette(mid);")
        selection_row.addWidget(self.selection_label, 1)
        self.edit_selected_button = QPushButton(
            "Редактировать выбранное (0)"
        )
        self.edit_selected_button.clicked.connect(
            self._edit_selected_clicked
        )
        self.edit_selected_button.setEnabled(False)
        selection_row.addWidget(self.edit_selected_button)
        layout.addLayout(selection_row)

        self.tree = _DropTreeWidget()
        self.tree.drop_handler = self
        self.tree.setHeaderLabels(
            ["Выбор", "Звук", "Состояние"] + list(FIELD_HEADERS)
        )
        self.tree.setTreePosition(NAME_COLUMN)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setMinimumSectionSize(60)
        self._fit_header_widths()
        self.tree.setSortingEnabled(False)
        self.tree.setColumnHidden(SELECT_COLUMN, True)
        self.tree.setSelectionMode(QAbstractItemView.NoSelection)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tree.setItemDelegate(SoundFieldDelegate(self, self.tree))
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(
            self._on_tree_context_menu
        )
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        self.tree.itemClicked.connect(self._on_item_clicked)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemExpanded.connect(self._on_expanded)

        self.status = QLabel("")
        self.status.setStyleSheet("color: palette(mid);")

        top_panel = QWidget()
        top_layout = QVBoxLayout(top_panel)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(4)
        top_layout.addWidget(self.tree, 1)
        top_layout.addWidget(self.status)

        pinned_panel = QWidget()
        pinned_layout = QVBoxLayout(pinned_panel)
        pinned_layout.setContentsMargins(0, 4, 0, 0)
        pinned_layout.setSpacing(4)
        pinned_header = QHBoxLayout()
        self.pinned_label = QLabel("Закреплённые файлы (0)")
        self.pinned_label.setStyleSheet("font-weight: 600;")
        pinned_header.addWidget(self.pinned_label)
        pinned_header.addStretch(1)
        self.unpin_all_button = QPushButton("Открепить все")
        self.unpin_all_button.setEnabled(False)
        self.unpin_all_button.clicked.connect(self._unpin_all)
        pinned_header.addWidget(self.unpin_all_button)
        pinned_layout.addLayout(pinned_header)

        self.pinned_list = _DropTreeWidget()
        self.pinned_list.drop_handler = self
        self.pinned_list.setHeaderLabels(
            ["Выбор", "Файл", "Состояние"] + list(FIELD_HEADERS)
        )
        self.pinned_list.setTreePosition(NAME_COLUMN)
        self.pinned_list.setRootIsDecorated(False)
        self.pinned_list.setAlternatingRowColors(True)
        self.pinned_list.setColumnWidth(SELECT_COLUMN, 70)
        self.pinned_list.setColumnWidth(NAME_COLUMN, 320)
        self.pinned_list.setColumnWidth(STATE_COLUMN, 100)
        self.pinned_list.setColumnWidth(3, 65)
        self.pinned_list.setColumnWidth(4, 85)
        self.pinned_list.setColumnWidth(5, 85)
        self.pinned_list.setColumnWidth(6, 90)
        self.pinned_list.setColumnWidth(7, 180)
        self.pinned_list.setColumnWidth(8, 100)
        self.pinned_list.setColumnHidden(SELECT_COLUMN, True)
        self.pinned_list.setSelectionMode(QAbstractItemView.NoSelection)
        self.pinned_list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.pinned_list.setItemDelegate(
            SoundFieldDelegate(self, self.pinned_list)
        )
        self.pinned_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.pinned_list.customContextMenuRequested.connect(
            self._on_pinned_context_menu
        )
        self.pinned_list.itemDoubleClicked.connect(
            self._on_pinned_double_click
        )
        self.pinned_list.itemClicked.connect(self._on_item_clicked)
        self.pinned_list.itemChanged.connect(self._on_item_changed)
        pinned_layout.addWidget(self.pinned_list, 1)

        self.splitter = QSplitter(Qt.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(6)
        self.splitter.addWidget(top_panel)
        self.splitter.addWidget(pinned_panel)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([420, 160])
        layout.addWidget(self.splitter, 1)

        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(DEFAULT_SEARCH_DELAY_MS)
        self._filter_timer.timeout.connect(self._run_filter)

        self._fields_timer = QTimer(self)
        self._fields_timer.setSingleShot(True)
        self._fields_timer.setInterval(0)
        self._fields_timer.timeout.connect(self._load_visible_fields)

        self._apply_tree_theme()
        self._update_project_state()

    def _apply_tree_theme(self):
        selection = self.ctx.ui.theme_color("selection", "#2E436E")
        bright = self.ctx.ui.theme_color("text_bright", "#FFFFFF")
        hover = self.ctx.ui.theme_color("hover", "#3A3D41")

        stylesheet = (
            "QTreeWidget::item:selected,"
            "QTreeWidget::item:selected:active,"
            "QTreeWidget::item:selected:!active,"
            "QTreeView::item:selected,"
            "QTreeView::item:selected:active,"
            "QTreeView::item:selected:!active {"
            " background-color: %s;"
            " color: %s;"
            "}"
            "QTreeWidget::item:hover, QTreeView::item:hover {"
            " background-color: %s;"
            "}"
            % (selection, bright, hover)
        )

        for widget in (self.tree, self.pinned_list):
            palette = widget.palette()
            color = QColor(selection)
            text = QColor(bright)
            for group in (QPalette.Active, QPalette.Inactive):
                palette.setColor(group, QPalette.Highlight, color)
                palette.setColor(group, QPalette.HighlightedText, text)
            widget.setPalette(palette)
            widget.setStyleSheet(stylesheet)

    def _fit_header_widths(self):
        labels = ["Выбор", "Звук", "Состояние"] + list(FIELD_HEADERS)
        minimums = {
            SELECT_COLUMN: 70,
            NAME_COLUMN: 360,
            STATE_COLUMN: 100,
            3: 65,
            4: 80,
            5: 80,
            6: 85,
            7: 170,
            8: 95,
        }
        header = self.tree.header()
        for index, label in enumerate(labels):
            width = max(
                header.sectionSizeHint(index),
                minimums.get(index, 60),
            )
            self.tree.setColumnWidth(index, width)

    def _update_project_state(self):
        active = bool(self.ctx.ui.is_project_active())
        self._apply_toolbar_state(active)
        if active:
            self.add_button.setToolTip(
                "Импортировать внешний .ogg в черновики проекта"
            )
            self.multi_button.setToolTip(
                "Выбрать файлы и папки для массовой правки"
            )
        else:
            self.add_button.setToolTip(
                "Создайте или загрузите проект, чтобы добавлять звуки"
            )
            self.multi_button.setToolTip(
                "Создайте или загрузите проект для мультиредактирования"
            )
            if self._selection_mode:
                self.exit_selection_mode()

    def _apply_toolbar_state(self, active=None):
        """Обновить доступность кнопок с учётом режима выбора."""
        if active is None:
            active = bool(self.ctx.ui.is_project_active())
        locked = self._selection_mode
        self.refresh_button.setEnabled(not locked)
        self.section_button.setEnabled(not locked)
        self.add_button.setEnabled(active and not locked)
        self.multi_button.setEnabled(active)

    # ------------------------------------------------------------ закреплённые
    @staticmethod
    def _path_key(path) -> str:
        return sdk_path_key(path)

    def _pinned_label_for(self, path) -> str:
        path = Path(path)
        if self._game_root is not None:
            try:
                relative = path.relative_to(Path(self._game_root) / "sounds")
                return str(relative)
            except ValueError:
                pass
        return str(path)

    def is_pinned(self, path) -> bool:
        return self._path_key(path) in self._pinned_keys

    def pin_paths(self, paths):
        added = 0
        for path in paths:
            path = Path(path)
            key = self._path_key(path)
            if key in self._pinned_keys:
                continue

            item = QTreeWidgetItem(
                [""] * (3 + len(FIELD_HEADERS))
            )
            item.setText(NAME_COLUMN, self._pinned_label_for(path))
            item.setData(0, Qt.UserRole, str(path))
            item.setData(
                0,
                _NODE_ROLE,
                SoundNode(
                    name=path.name,
                    path=path,
                    is_folder=False,
                ),
            )
            item.setData(0, _FIELDS_ROLE, False)
            item.setFlags(item.flags() | Qt.ItemIsEditable)
            item.setIcon(
                NAME_COLUMN,
                self.style().standardIcon(QStyle.SP_FileIcon),
            )
            item.setToolTip(NAME_COLUMN, str(path))
            for column in FIELD_COLUMNS:
                item.setText(column, "…")

            cached = self._fields_cache.get(str(path))
            if cached is not None:
                self._apply_fields_to_item(
                    item,
                    cached[0],
                    cached[1],
                )
                item.setData(0, _FIELDS_ROLE, True)

            if self._selection_mode:
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                state = (
                    Qt.Checked
                    if str(path) in self._selected_files
                    else Qt.Unchecked
                )
                item.setCheckState(SELECT_COLUMN, state)
                self._paint_selection_state(item, state)

            self.pinned_list.addTopLevelItem(item)
            self._pinned_keys.add(key)
            added += 1

        self._refresh_pinned_label()
        if added:
            self.splitter.setSizes([420, max(160, self.splitter.sizes()[1])])
            self._fields_timer.start()
        return added

    def unpin_path(self, path):
        key = self._path_key(path)
        if key not in self._pinned_keys:
            return False
        self._pinned_keys.discard(key)
        for index in range(self.pinned_list.topLevelItemCount() - 1, -1, -1):
            item = self.pinned_list.topLevelItem(index)
            if self._path_key(item.data(0, Qt.UserRole)) == key:
                self.pinned_list.takeTopLevelItem(index)
                break
        self._refresh_pinned_label()
        return True

    def pinned_paths(self):
        return [
            Path(self.pinned_list.topLevelItem(index).data(0, Qt.UserRole))
            for index in range(self.pinned_list.topLevelItemCount())
        ]

    def _refresh_pinned_label(self):
        count = self.pinned_list.topLevelItemCount()
        self.pinned_label.setText("Закреплённые файлы (%d)" % count)
        self.unpin_all_button.setEnabled(count > 0)

    def _unpin_all(self):
        if self.pinned_list.topLevelItemCount() == 0:
            return
        self.pinned_list.clear()
        self._pinned_keys.clear()
        self._refresh_pinned_label()
        self.ctx.ui.set_status("Все закреплённые файлы откреплены", 3000)

    def _on_pinned_double_click(self, item, column=0):
        if column in FIELD_COLUMNS:
            self._try_edit_cell(item, column)
            return
        if column == NAME_COLUMN:
            path = item.data(0, Qt.UserRole)
            if path:
                self._on_open(Path(path))

    def _on_pinned_context_menu(self, position):
        item = self.pinned_list.itemAt(position)
        if item is None:
            return
        path = Path(item.data(0, Qt.UserRole))

        menu = QMenu(self)
        open_action = menu.addAction("Открыть")
        copy_action = menu.addAction("Копировать путь к")
        unpin_action = menu.addAction("Открепить")
        chosen = menu.exec_(self.pinned_list.viewport().mapToGlobal(position))
        if chosen is open_action:
            self._on_open(path)
        elif chosen is copy_action:
            self._copy_sound_path(path)
        elif chosen is unpin_action:
            self.unpin_path(path)

    def _sound_ref_path(self, path) -> str:
        """Путь звука в VFS без .ogg: weapons\\kar98k\\kar98k_shoot."""
        path = Path(path)
        relative = path
        if self._game_root is not None:
            try:
                relative = path.relative_to(Path(self._game_root) / "sounds")
            except ValueError:
                relative = path
        return "\\".join(relative.with_suffix("").parts)

    def _copy_sound_path(self, path):
        reference = self._sound_ref_path(path)
        self.ctx.ui.copy_to_clipboard(reference)
        self.ctx.ui.set_status(
            "Скопирован путь: %s" % reference,
            3000,
        )

    def _on_tree_context_menu(self, position):
        item = self.tree.itemAt(position)
        if item is None or self.tree.columnAt(position.x()) != NAME_COLUMN:
            return
        node = item.data(0, _NODE_ROLE)
        if node is None or node.is_folder:
            return
        path = Path(node.path)

        menu = QMenu(self)
        open_action = menu.addAction("Открыть")
        copy_action = menu.addAction("Копировать путь к")
        if self.is_pinned(path):
            pin_action = menu.addAction("Открепить")
        else:
            pin_action = menu.addAction("Закрепить")

        chosen = menu.exec_(self.tree.viewport().mapToGlobal(position))
        if chosen is open_action:
            self._on_open(path)
        elif chosen is copy_action:
            self._copy_sound_path(path)
        elif chosen is pin_action:
            if self.is_pinned(path):
                self.unpin_path(path)
            else:
                self.pin_paths([path])
                self.ctx.ui.set_status(
                    "Файл закреплён: %s" % path.name,
                    3000,
                )

    # ------------------------------------------------------------ приём предмета
    def dragEnterEvent(self, event):
        if self.can_accept_drop(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self.can_accept_drop(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        if self.handle_item_drop(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def can_accept_drop(self, mime) -> bool:
        return mime is not None and mime.hasFormat(ITEM_SECTION_MIME)

    def handle_item_drop(self, mime) -> bool:
        if not self.can_accept_drop(mime):
            return False

        payload = decode_item_section(mime.data(ITEM_SECTION_MIME))
        if payload is None:
            return False

        if self._game_root is None:
            self.ctx.ui.set_status(
                "Сначала выполните «Пересобрать VFS»",
                5000,
            )
            return True

        paths = extract_sound_paths(
            payload.fields,
            Path(self._game_root) / "sounds",
        )
        if not paths:
            self.ctx.ui.set_status(
                "В секции %s не найдены .ogg" % payload.section,
                5000,
            )
            return True

        added = self.pin_paths(paths)
        self.ctx.ui.set_status(
            "Из секции %s закреплено файлов: %d"
            % (payload.section, added),
            5000,
        )
        return True

    def _open_section_clicked(self):
        if self._on_open_section is None:
            return
        self._on_open_section()

    # ------------------------------------------------------------ данные
    def set_game_root(self, root):
        self._game_root = Path(root) if root else None
        self.path_label.setText(
            "VFS: %s" % (Path(root) / "sounds")
            if root
            else "Каталог звуков не определён: выполните «Пересобрать VFS»"
        )
        if root:
            self.reload()
        else:
            if self._selection_mode:
                self.exit_selection_mode()
            self._scan_generation += 1
            self._filter_generation += 1
            self._fields_generation += 1
            self._entries = ()
            self._tree_data = ()
            self._fields_cache.clear()
            self._fields_pending.clear()
            self._selected_files.clear()
            self.tree.clear()
            self.status.setText("")

    def reload(self):
        self._update_project_state()
        self._fields_generation += 1
        self._fields_cache.clear()
        self._fields_pending.clear()

        # Пересоздать закреплённые строки: у них мог измениться кэш полей.
        pinned = self.pinned_paths()
        if pinned:
            self.pinned_list.clear()
            self._pinned_keys.clear()
            self.pin_paths(pinned)
        self._refresh_pinned_label()

        if self._game_root is None:
            self.tree.clear()
            self.status.setText("Сначала выполните «Пересобрать VFS».")
            return

        sounds_root = Path(self._game_root) / "sounds"
        drafts = []
        try:
            for draft in self.ctx.ui.project_asset_drafts():
                path = Path(draft.path)
                try:
                    path.relative_to(sounds_root)
                except ValueError:
                    continue
                drafts.append(
                    SoundEntry(
                        path=path,
                        is_new=bool(draft.is_new),
                        changed=bool(draft.changed),
                    )
                )
        except Exception as exc:
            self.ctx.log("ogg_editor: не удалось прочитать черновики: %s" % exc)

        self._scan_generation += 1
        generation = self._scan_generation
        self.status.setText("Сканирование звуков…")

        def work(log):
            entries = scan_sounds(sounds_root, drafts)
            nodes = build_sound_tree(entries, sounds_root)
            return SoundTreeResult(tuple(entries), nodes)

        self.ctx.tasks.run(
            work,
            on_done=lambda result, error: self._on_scan_done(
                generation,
                result,
                error,
            ),
        )

    def _on_scan_done(self, generation, result, error):
        if generation != self._scan_generation:
            return
        if error is not None:
            self.status.setText("Ошибка сканирования: %s" % error)
            self.ctx.log("ogg_editor: ошибка сканирования: %s" % error)
            return

        self._entries = tuple(result.entries or ())
        self._tree_data = tuple(result.nodes or ())

        # Черновики могли исчезнуть, пока шло сканирование.
        valid = {str(entry.path) for entry in self._entries}
        self._selected_files &= valid

        new_count = sum(1 for item in self._entries if item.is_new)
        changed_count = sum(1 for item in self._entries if item.changed)
        self.status.setText(
            "Найдено .ogg: %d | изменено: %d | новых: %d"
            % (len(self._entries), changed_count, new_count)
        )

        if self.filter_edit.text().strip():
            self._run_filter()
        else:
            self._render_tree(self._tree_data)

    # ------------------------------------------------------------ фильтр
    def _schedule_filter(self, _text):
        self._filter_timer.start()

    def _run_filter(self):
        query = self.filter_edit.text().strip()
        self._filter_generation += 1
        generation = self._filter_generation

        if not query:
            self._render_tree(self._tree_data)
            return

        data = self._tree_data
        self.status.setText("Фильтрация…")

        def work(log):
            return filter_sound_tree(data, query)

        self.ctx.tasks.run(
            work,
            on_done=lambda result, error: self._on_filter_done(
                generation,
                result,
                error,
            ),
        )

    def _on_filter_done(self, generation, nodes, error):
        if generation != self._filter_generation:
            return
        if error is not None:
            self.status.setText("Ошибка фильтра: %s" % error)
            return

        self._render_tree(tuple(nodes or ()))
        self.status.setText(
            "Найдено .ogg: %d (фильтр: %s)"
            % (len(self._entries), self.filter_edit.text().strip())
        )

    # ------------------------------------------------------------ отрисовка
    def _render_tree(self, nodes):
        self.tree.clear()
        for node in nodes:
            self._add_node(None, node)
        if self._selection_mode:
            self._update_selection_summary()
        self._fields_timer.start()

    def _add_node(self, parent_item, node: SoundNode):
        item = QTreeWidgetItem(
            [""] * (3 + len(FIELD_HEADERS))
        )
        item.setText(NAME_COLUMN, node.name)

        if node.is_folder:
            item.setData(0, Qt.UserRole, None)
            item.setData(0, Qt.UserRole + 1, str(node.path))
            item.setData(0, _NODE_ROLE, node)
            item.setData(0, _LOADED_ROLE, False)
            item.setIcon(
                NAME_COLUMN,
                self.style().standardIcon(QStyle.SP_DirIcon),
            )
            item.setToolTip(NAME_COLUMN, str(node.path))
            if node.children:
                item.addChild(
                    QTreeWidgetItem(
                        [""] * (len(FIELD_HEADERS) + 3)
                    )
                )
        else:
            entry = node.entry
            item.setText(
                STATE_COLUMN,
                _entry_state(entry) if entry else "",
            )
            item.setData(0, Qt.UserRole, str(node.path))
            item.setData(0, _NODE_ROLE, node)
            item.setData(0, _FIELDS_ROLE, False)
            item.setFlags(item.flags() | Qt.ItemIsEditable)
            for column in FIELD_COLUMNS:
                item.setText(column, "…")
            cached = self._fields_cache.get(str(node.path))
            if cached is not None:
                self._apply_fields_to_item(
                    item,
                    cached[0],
                    cached[1],
                )
                item.setData(0, _FIELDS_ROLE, True)

            item.setIcon(
                NAME_COLUMN,
                self.style().standardIcon(QStyle.SP_FileIcon),
            )
            tooltip = str(node.path)
            if entry is not None and entry.is_new:
                tooltip += "\nНовый файл проекта"
            elif entry is not None and entry.changed:
                tooltip += "\nИзменён в проекте"
            item.setToolTip(NAME_COLUMN, tooltip)
            if entry is not None and (entry.changed or entry.is_new):
                item.setForeground(
                    NAME_COLUMN,
                    QBrush(
                        QColor(NEW_COLOR if entry.is_new else CHANGED_COLOR)
                    ),
                )

        if self._selection_mode:
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            state = self._state_for_node(node)
            item.setCheckState(SELECT_COLUMN, state)
            self._paint_selection_state(item, state)

        if parent_item is None:
            self.tree.addTopLevelItem(item)
        else:
            parent_item.addChild(item)
        return item

    def _on_expanded(self, item):
        if item.data(0, _LOADED_ROLE) is True:
            return
        node = item.data(0, _NODE_ROLE)
        if node is None or not node.is_folder:
            return

        item.takeChildren()
        for child in node.children:
            self._add_node(item, child)
        item.setData(0, _LOADED_ROLE, True)
        self._fields_timer.start()

    # ------------------------------------------------------------ поля
    @staticmethod
    def _format_number(value) -> str:
        try:
            text = ("%.3f" % float(value)).rstrip("0").rstrip(".")
        except (TypeError, ValueError):
            return "-"
        return text or "0"

    def _apply_fields_to_item(self, item, params, error):
        item.setData(
            0,
            _PARAMS_ROLE,
            None if error else params,
        )
        item.setData(
            0,
            _FIELDS_ERROR_ROLE,
            str(error) if error else "",
        )
        for column in FIELD_COLUMNS:
            item.setToolTip(column, "")

        if error:
            item.setText(FIELD_COLUMNS[0], "!")
            for column in FIELD_COLUMNS[1:]:
                item.setText(column, "-")
            item.setToolTip(FIELD_COLUMNS[0], str(error))
            return

        if params is None:
            for column in FIELD_COLUMNS:
                item.setText(column, "-")
            item.setToolTip(
                FIELD_COLUMNS[0],
                "Комментарий не в формате X-Ray",
            )
            return

        item.setText(FIELD_COLUMNS[0], str(params.version))
        item.setText(FIELD_COLUMNS[1], self._format_number(params.min_dist))
        item.setText(FIELD_COLUMNS[2], self._format_number(params.max_dist))
        item.setText(
            FIELD_COLUMNS[3],
            self._format_number(params.base_volume),
        )
        item.setText(
            FIELD_COLUMNS[4],
            sound_type_short(params.game_type),
        )
        item.setToolTip(
            FIELD_COLUMNS[4],
            sound_type_tooltip(params.game_type),
        )
        item.setText(
            FIELD_COLUMNS[5],
            self._format_number(params.max_ai_dist),
        )

    @staticmethod
    def _read_comment_fields(path, data=None):
        candidates = []

        if data is not None:
            payload = bytes(data)
            candidates.append(payload[:_HEADER_READ_LIMIT])
            if len(payload) > _HEADER_READ_LIMIT:
                candidates.append(payload)
        else:
            path = Path(path)
            with path.open("rb") as handle:
                candidates.append(handle.read(_HEADER_READ_LIMIT))
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            if (
                size
                and size <= _FULL_FALLBACK_LIMIT
                and len(candidates[0]) < size
            ):
                candidates.append(path.read_bytes())

        last_error = None
        for payload in candidates:
            try:
                comments = read_comments_prefix(payload)
                return parse_xray_comment(comments.engine_comment), ""
            except OggError as exc:
                last_error = exc

        raise last_error or OggError("Не удалось прочитать комментарий")

    def _load_visible_fields(self):
        generation = self._fields_generation
        payloads = {}

        for item in self._iter_field_items():
            node = item.data(0, _NODE_ROLE)
            if node is None or node.is_folder:
                continue
            if item.data(0, _FIELDS_ROLE) is True:
                continue

            path = str(node.path)
            cached = self._fields_cache.get(path)
            if cached is not None:
                self._apply_fields_to_item(item, cached[0], cached[1])
                item.setData(0, _FIELDS_ROLE, True)
                continue

            if path in self._fields_pending:
                continue

            try:
                data = self.ctx.ui.read_project_asset(node.path)
            except Exception:
                data = None

            payloads[path] = (node.path, data)
            self._fields_pending.add(path)
            item.setData(0, _FIELDS_ROLE, "loading")

        if not payloads:
            return

        paths = set(payloads)

        def work(log):
            result = {}
            for path, (path_obj, data) in payloads.items():
                try:
                    result[path] = self._read_comment_fields(
                        path_obj,
                        data,
                    )
                except Exception as exc:
                    result[path] = (None, str(exc))
            return result

        self.ctx.tasks.run(
            work,
            on_done=lambda result, error: self._on_fields_done(
                generation,
                paths,
                result,
                error,
            ),
        )

    def _on_fields_done(self, generation, paths, result, error):
        self._fields_pending.difference_update(paths)
        if generation != self._fields_generation:
            return

        values = dict(result or {})
        if error is not None:
            for path in paths:
                values[path] = (None, str(error))

        for path, value in values.items():
            self._fields_cache[path] = value

        for item in self._iter_field_items():
            node = item.data(0, _NODE_ROLE)
            if node is None or node.is_folder:
                continue
            value = values.get(str(node.path))
            if value is None:
                continue
            self._apply_fields_to_item(item, value[0], value[1])
            item.setData(0, _FIELDS_ROLE, True)

    # ------------------------------------------------------------ правка ячейки
    def apply_cell_edit(self, item, column, value):
        if self._selection_mode:
            return
        node = item.data(0, _NODE_ROLE)
        if node is None or node.is_folder:
            return

        if not self._require_project():
            return

        params = item.data(0, _PARAMS_ROLE) or XraySoundComment()

        try:
            if column == FIELD_COLUMNS[0]:
                version = int(value)
                if version not in (1, 2, 3):
                    raise ValueError("версия должна быть 1, 2 или 3")
                params = replace(params, version=version)
            elif column == FIELD_COLUMNS[1]:
                number = float(value)
                if number <= 0:
                    raise ValueError("Min distance должен быть больше нуля")
                params = replace(params, min_dist=number)
            elif column == FIELD_COLUMNS[2]:
                number = float(value)
                if number <= 0:
                    raise ValueError("Max distance должен быть больше нуля")
                params = replace(params, max_dist=number)
            elif column == FIELD_COLUMNS[3]:
                number = float(value)
                if number < 0:
                    raise ValueError("Base volume не может быть отрицательным")
                params = replace(params, base_volume=number)
            elif column == FIELD_COLUMNS[4]:
                params = replace(params, game_type=int(value))
            elif column == FIELD_COLUMNS[5]:
                number = float(value)
                if number < 0:
                    raise ValueError("Max AI distance не может быть отрицательным")
                params = replace(params, max_ai_dist=number)
            else:
                return
        except (TypeError, ValueError) as exc:
            self.ctx.ui.show_error("Некорректное значение: %s" % exc)
            return

        if self._on_edit_field is None:
            self.ctx.ui.show_error(
                "Модуль не умеет сохранять параметры из дерева"
            )
            return

        ok, error = self._on_edit_field(Path(node.path), params)
        if not ok:
            self.ctx.ui.show_error(
                error or "Не удалось изменить параметры файла"
            )
            return

        path = str(node.path)
        self._fields_cache[path] = (params, "")

        for current in self._iter_field_items():
            current_node = current.data(0, _NODE_ROLE)
            if (
                current_node is None
                or current_node.is_folder
                or str(current_node.path) != path
            ):
                continue
            self._apply_fields_to_item(current, params, "")
            current.setData(0, _FIELDS_ROLE, True)
            if current.text(STATE_COLUMN) != "новый":
                current.setText(STATE_COLUMN, "изменён")
                current.setForeground(
                    NAME_COLUMN,
                    QBrush(QColor(CHANGED_COLOR)),
                )

        self.status.setText("Параметры обновлены: %s" % node.name)

    # ------------------------------------------------------------ выбор
    def _iter_items(self):
        stack = [
            self.tree.topLevelItem(index)
            for index in range(self.tree.topLevelItemCount())
        ]
        while stack:
            item = stack.pop()
            yield item
            stack.extend(
                item.child(index) for index in range(item.childCount())
            )

    def _iter_field_items(self):
        yield from self._iter_items()
        for index in range(self.pinned_list.topLevelItemCount()):
            yield self.pinned_list.topLevelItem(index)

    @staticmethod
    def _is_folder(item) -> bool:
        node = item.data(0, _NODE_ROLE)
        return bool(node is not None and node.is_folder)

    def _state_for_node(self, node: SoundNode):
        if not node.is_folder:
            return (
                Qt.Checked
                if str(node.path) in self._selected_files
                else Qt.Unchecked
            )

        paths = [str(item.path) for item in iter_file_nodes(node)]
        if not paths:
            return Qt.Unchecked

        selected = sum(
            1 for path in paths if path in self._selected_files
        )
        if selected == len(paths):
            return Qt.Checked
        if selected:
            return Qt.PartiallyChecked
        return Qt.Unchecked

    def _selection_brushes(self):
        selection = QColor(
            self.ctx.ui.theme_color("selection", "#2E436E")
        )
        partial = QColor(selection)
        partial.setAlpha(110)
        return QBrush(selection), QBrush(partial), QBrush()

    def _paint_selection_state(self, item, state, brushes=None):
        """Подсветить строку, отмеченную для мультиредактирования."""
        if brushes is None:
            brushes = self._selection_brushes()
        checked, partial, cleared = brushes

        if not self._selection_mode:
            mark = 0
        elif state == Qt.Checked:
            mark = 1
        elif state == Qt.PartiallyChecked:
            mark = 2
        else:
            mark = 0

        if mark == 1:
            brush = checked
        elif mark == 2:
            brush = partial
        else:
            brush = cleared

        item.setData(0, _SELECTED_ROLE, mark)
        for column in range(3 + len(FIELD_HEADERS)):
            item.setBackground(column, brush)

    def _prepare_checkboxes(self):
        brushes = self._selection_brushes()
        self._updating_selection = True
        try:
            for item in self._iter_field_items():
                node = item.data(0, _NODE_ROLE)
                if node is None:
                    continue
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                state = self._state_for_node(node)
                item.setCheckState(SELECT_COLUMN, state)
                self._paint_selection_state(item, state, brushes)
        finally:
            self._updating_selection = False

    def _refresh_checkstates(self):
        brushes = self._selection_brushes()
        previous = self._updating_selection
        self._updating_selection = True
        try:
            for item in self._iter_field_items():
                node = item.data(0, _NODE_ROLE)
                if node is not None:
                    state = self._state_for_node(node)
                    item.setCheckState(SELECT_COLUMN, state)
                    self._paint_selection_state(item, state, brushes)
        finally:
            self._updating_selection = previous

    def _toggle_selection_mode(self, _checked=False):
        if self._selection_mode:
            self.exit_selection_mode()
        else:
            self.enter_selection_mode()

    def enter_selection_mode(self):
        if not self.ctx.ui.is_project_active():
            return
        self._selection_mode = True
        self._selected_files.clear()
        self.multi_button.setChecked(True)
        self.multi_button.setText("Отменить выбор")
        self._style_cancel_selection_button(True)
        self.tree.setColumnHidden(SELECT_COLUMN, False)
        self.pinned_list.setColumnHidden(SELECT_COLUMN, False)
        self._prepare_checkboxes()
        self._apply_toolbar_state()
        self._update_selection_summary()

    def exit_selection_mode(self):
        self._selection_mode = False
        self._selected_files.clear()
        self.multi_button.setChecked(False)
        self.multi_button.setText("Мультиредактирование")
        self._style_cancel_selection_button(False)
        self.tree.setColumnHidden(SELECT_COLUMN, True)
        self.pinned_list.setColumnHidden(SELECT_COLUMN, True)
        self._refresh_checkstates()
        self._apply_toolbar_state()
        self._update_selection_summary()

    def _style_cancel_selection_button(self, active):
        """Красный акцент для кнопки выхода из мультиредактирования."""
        button = self.multi_button
        if not active:
            button.setStyleSheet("")
            return

        accent = self.ctx.ui.theme_color("accent_red", "#F14C4C")
        button.setStyleSheet(
            "QPushButton {"
            " color: %s;"
            " border: 1px solid %s;"
            " font-weight: 600;"
            "}"
            "QPushButton:hover {"
            " color: %s;"
            " border-color: %s;"
            "}"
            % (accent, accent, accent, accent)
        )

    def _on_item_clicked(self, item, column):
        if not self._selection_mode or column == SELECT_COLUMN:
            return
        state = item.checkState(SELECT_COLUMN)
        item.setCheckState(
            SELECT_COLUMN,
            Qt.Unchecked if state == Qt.Checked else Qt.Checked,
        )

    def _on_item_changed(self, item, column):
        if (
            not self._selection_mode
            or column != SELECT_COLUMN
            or self._updating_selection
        ):
            return

        node = item.data(0, _NODE_ROLE)
        if node is None:
            return

        state = item.checkState(SELECT_COLUMN)
        if state == Qt.PartiallyChecked:
            state = Qt.Checked

        if node.is_folder:
            paths = [str(entry.path) for entry in iter_file_nodes(node)]
            if state == Qt.Checked:
                self._selected_files.update(paths)
            else:
                self._selected_files.difference_update(paths)
        else:
            path = str(node.path)
            if state == Qt.Checked:
                self._selected_files.add(path)
            else:
                self._selected_files.discard(path)

        self._refresh_checkstates()
        self._update_selection_summary()

    def selected_files(self):
        return [
            Path(path)
            for path in sorted(
                self._selected_files,
                key=lambda value: str(value).lower(),
            )
        ]

    def _update_selection_summary(self):
        if not self._selection_mode:
            self.selection_label.setText("")
            self.edit_selected_button.setEnabled(False)
            self.edit_selected_button.setText(
                "Редактировать выбранное (0)"
            )
            self._style_edit_selected_button(False)
            return

        count = len(self._selected_files)
        self.selection_label.setText(
            "Выбрано файлов: %d. Клик по папке выбирает её целиком; "
            "повторный клик снимает выбор." % count
        )
        self.edit_selected_button.setText(
            "Редактировать выбранное (%d)" % count
        )
        self.edit_selected_button.setEnabled(count > 0)
        self._style_edit_selected_button(count > 0)

    def _style_edit_selected_button(self, active):
        """Подсветить кнопку акцентом, как новую строку лога."""
        button = self.edit_selected_button
        if not active:
            button.setStyleSheet("")
            return

        accent = self.ctx.ui.theme_color("accent_amber", "#CCA700")
        button.setStyleSheet(
            "QPushButton {"
            " color: %s;"
            " border: 1px solid %s;"
            " font-weight: 600;"
            "}"
            "QPushButton:hover {"
            " color: %s;"
            " border-color: %s;"
            "}"
            "QPushButton:disabled {"
            " color: %s;"
            " border-color: %s;"
            "}"
            % (accent, accent, accent, accent, accent, accent)
        )

    def _edit_selected_clicked(self):
        paths = self.selected_files()
        if paths and self._on_multi_edit is not None:
            self._on_multi_edit(paths)

    # ------------------------------------------------------------ действия
    def _require_project(self) -> bool:
        """Показать окно, если проект не создан/не загружен."""
        if self.ctx.ui.is_project_active():
            return True

        QMessageBox.warning(
            self,
            "Создайте проект",
            "Создайте или загрузите проект, чтобы менять параметры.",
            QMessageBox.Ok,
        )
        return False

    def _try_edit_cell(self, item, column):
        if self._selection_mode:
            return
        if not self._require_project():
            return
        if item.data(0, _FIELDS_ROLE) is not True:
            self.ctx.ui.set_status(
                "Параметры ещё загружаются, подождите…",
                3000,
            )
            return
        error = item.data(0, _FIELDS_ERROR_ROLE)
        if error:
            self.ctx.ui.show_error(str(error))
            return

        if column == FIELD_COLUMNS[4]:
            self._open_type_picker(item, column)
            return

        tree = item.treeWidget()
        if tree is not None:
            tree.editItem(item, column)

    def _open_type_picker(self, item, column):
        params = item.data(0, _PARAMS_ROLE) or XraySoundComment()
        tree = item.treeWidget()
        if tree is None:
            return

        if self._type_picker is not None:
            try:
                self._type_picker.hide()
                self._type_picker.deleteLater()
            except Exception:
                pass
            self._type_picker = None

        picker = SoundTypePicker(
            self,
            current_value=params.game_type,
        )
        self._type_picker = picker

        def apply(value):
            self.apply_cell_edit(item, column, value)
            self._type_picker = None
            picker.deleteLater()

        picker.selected.connect(apply)
        rect = tree.visualItemRect(item)
        anchor = tree.viewport().mapToGlobal(
            QPoint(rect.left(), rect.bottom())
        )
        picker.show_at(anchor)

    def _on_double_click(self, item, column):
        if self._selection_mode:
            return

        node = item.data(0, _NODE_ROLE)
        if node is None or node.is_folder:
            return

        if column in FIELD_COLUMNS:
            self._try_edit_cell(item, column)
            return

        if column == NAME_COLUMN:
            path = item.data(0, Qt.UserRole)
            if path:
                self._on_open(Path(path))

    def _add_clicked(self):
        self._on_add()


class SoundOpenHandler:
    """Открывать .ogg во вкладке модуля из общего маршрутизатора."""

    priority = 80

    def __init__(self, module):
        self.module = module

    def can_open(self, path):
        try:
            return Path(path).suffix.lower() == ".ogg"
        except (TypeError, ValueError):
            return False

    def open(self, path, api):
        api.commands.execute("ogg_editor.open", path)
        return True

