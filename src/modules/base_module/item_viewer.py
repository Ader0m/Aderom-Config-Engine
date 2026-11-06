# -*- coding: utf-8 -*-
"""Просмотр игровых предметов.

Возможность базового модуля:

- ``Инструменты → Просмотр игровых предметов``;
- каталог собирается ядром из резолва ``system.ltx`` (с учётом
  ``mod_system_*.ltx``) через ``ctx.services``;
- иконки вырезаются из атласа ``icons_texture`` по ``inv_grid_*``;
- если текстура не найдена, показывается серый зачёркнутый круг 50×50.

Модуль не импортирует ``core`` и ``ui``: только SDK и PyQt для своего
окна. Тяжёлая работа (резолв, разбор LTX, декодирование DDS) выполняется
в ``ctx.tasks``.
"""

from __future__ import annotations

from PyQt5.QtCore import QSize, QSortFilterProxyModel, Qt, QTimer
from PyQt5.QtGui import (
    QColor,
    QIcon,
    QImage,
    QPainter,
    QPen,
    QPixmap,
    QStandardItem,
    QStandardItemModel,
)
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

WINDOW_KEY = "base.item_viewer"
PROGRESS_KEY = "base.item_viewer.progress"
GRID_SIZE = 50
GALLERY_ICON_SIZE = 100
PLACEHOLDER_SIZE = 50

ENTRY_ROLE = Qt.UserRole + 1
SEARCH_ROLE = Qt.UserRole + 2
KIND_ROLE = Qt.UserRole + 3


class KindListWidget(QListWidget):
    """Список kind, который не сбрасывает выбор по клику в пустоте."""

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.itemAt(event.pos()) is None:
            event.accept()
            return
        super().mousePressEvent(event)


class ItemFilterProxy(QSortFilterProxyModel):
    """Фильтр по тексту и набору выбранных kind."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._needle = ""
        self._kinds = None

    def set_filters(self, needle, kinds):
        needle = str(needle or "").strip().lower()
        new_kinds = None if kinds is None else set(kinds)

        if needle == self._needle and new_kinds == self._kinds:
            return

        self._needle = needle
        self._kinds = new_kinds
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        index = self.sourceModel().index(row, 0, parent)
        if not index.isValid():
            return False

        if self._needle:
            haystack = str(index.data(SEARCH_ROLE) or "").lower()
            if self._needle not in haystack:
                return False

        if self._kinds is not None:
            kind = str(index.data(KIND_ROLE) or "")
            if kind not in self._kinds:
                return False

        return True


def _image_from_region(region) -> QImage | None:
    """Превратить SDK-регион RGBA в самостоятельный QImage."""
    if region is None:
        return None
    if region.width <= 0 or region.height <= 0 or not region.rgba:
        return None

    image = QImage(
        region.rgba,
        region.width,
        region.height,
        region.width * 4,
        QImage.Format_RGBA8888,
    )
    if image.isNull():
        return None
    return image.copy()


def _placeholder_pixmap(color: str, size: int = PLACEHOLDER_SIZE) -> QPixmap:
    """Серый зачёркнутый круг — заглушка отсутствующей иконки."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)

    pen = QPen(QColor(color))
    pen.setWidth(2)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)

    margin = 6
    painter.drawEllipse(margin, margin, size - margin * 2, size - margin * 2)
    painter.drawLine(
        margin + 4,
        size - margin - 4,
        size - margin - 4,
        margin + 4,
    )
    painter.end()

    return pixmap


class ItemViewerFeature:
    """Запрашивает каталог у ядра и показывает окно модуля."""

    WINDOW_KEY = WINDOW_KEY

    def __init__(self, ctx, session):
        self.ctx = ctx
        self.session = session
        self._window = None
        self._request_seq = 0
        self._running = False

    # -------------------------------------------------- жизненный цикл
    def show(self):
        if not self.session.has_tree:
            self.ctx.ui.show_error(
                "Дерево не построено: сначала «Чистый запуск»."
            )
            return

        if self._running:
            self.ctx.ui.log("Просмотр предметов уже выполняется.")
            return

        self._running = True
        self._request_seq += 1
        token = self._request_seq
        forest = self.session.forest

        self.ctx.ui.set_status("Предметы: сборка каталога…")
        self.ctx.ui.log("Просмотр игровых предметов: сборка каталога…")

        def work(log):
            catalog = self.ctx.services.build_item_catalog(forest, log=log)

            # Ядро декодирует иконки пакетно: кэш регионов + пул процессов.
            # Модуль только превращает готовые RGBA в QImage.
            icons = self.ctx.services.load_item_icons(catalog, log=log)
            images = {
                icon.section: _image_from_region(icon)
                for icon in icons
            }

            return catalog, images

        self.ctx.tasks.run(
            work,
            on_done=lambda result, err: self._on_done(result, err, token),
            on_log=self._on_log,
        )

    def close(self):
        if self._window is not None:
            self.ctx.ui.close_module_window(self.WINDOW_KEY)
            self._window = None

    # -------------------------------------------------- фоновая задача
    def _on_log(self, message):
        text = str(message)
        if text.startswith("item progress:"):
            self.ctx.ui.log_status(
                PROGRESS_KEY,
                "Предметы: %s" % text.split(":", 1)[1].strip(),
            )
            return
        self.ctx.ui.log(text)

    def _on_done(self, result, err, token):
        self._running = False

        if token != self._request_seq:
            return

        if err is not None:
            self.ctx.ui.set_status("Ошибка")
            self.ctx.ui.log(
                "Ошибка просмотра предметов: %s" % err
            )
            self.ctx.ui.show_error(
                "Не удалось собрать список предметов:\n%s" % err
            )
            return

        catalog, images = result

        self.ctx.ui.set_status(
            "Предметы: %d" % len(catalog.items),
            5000,
        )
        self.ctx.ui.log(
            "Предметы: %d секций из %s"
            % (
                len(catalog.items),
                catalog.source_path or "—",
            )
        )
        for warning in catalog.warnings:
            self.ctx.ui.log("Предметы: %s" % warning)

        if self._window is None:
            self._window = ItemViewerWindow(
                theme_color=self.ctx.ui.theme_color,
                on_refresh=self.show,
            )

        self._window.set_catalog(catalog, images)
        self.ctx.ui.show_module_window(self.WINDOW_KEY, self._window)


class ItemViewerWindow(QDialog):
    """Немодальное окно: список предметов слева, поля и иконка справа."""

    def __init__(self, theme_color=None, on_refresh=None, parent=None):
        super().__init__(parent)

        self._theme_color = theme_color or (
            lambda name, fallback=None: fallback
        )
        self._on_refresh = on_refresh
        self._catalog = None
        self._images = {}
        self._entries = []
        self._updating = False
        self._current_entry = None

        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(180)
        self._filter_timer.timeout.connect(self._apply_filter_now)

        self._placeholder = _placeholder_pixmap(
            self._palette("text_muted", "#787878")
        )

        self.setWindowTitle("Просмотр игровых предметов")
        self.resize(1180, 760)

        layout = QVBoxLayout(self)

        # -------------------------------------------------- шапка
        self.info_label = QLabel("Загрузка каталога…")
        self.info_label.setWordWrap(True)
        self.info_label.setProperty("role", "muted")
        layout.addWidget(self.info_label)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Фильтр:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText(
            "секция, kind, inv_name, описание…"
        )
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._schedule_filter)
        filter_row.addWidget(self.filter_edit, 1)
        layout.addLayout(filter_row)

        # -------------------------------------------------- основная область
        splitter = QSplitter(Qt.Horizontal)

        # Модель + прокси: фильтрация меняет только прокси и не вызывает
        # setHidden() для тысяч элементов (именно это давало мерцание).
        self.model = QStandardItemModel(self)
        self.proxy = ItemFilterProxy(self)
        self.proxy.setSourceModel(self.model)

        # Колонка группировки по kind. Это обычный список с
        # ExtendedSelection: выбранные строки просто подсвечиваются
        # штатным цветом выделения, без чекбоксов и иконок.
        self.kind_panel = QWidget()
        self.kind_panel.setMinimumWidth(190)
        kind_layout = QVBoxLayout(self.kind_panel)
        kind_layout.setContentsMargins(0, 0, 0, 0)
        kind_layout.setSpacing(4)

        kind_caption = QLabel("kind")
        kind_caption.setProperty("role", "muted")
        kind_layout.addWidget(kind_caption)

        self.kind_list = KindListWidget()
        # MultiSelection: обычный клик переключает только эту строку,
        # не сбрасывая остальные выбранные kind.
        self.kind_list.setSelectionMode(
            QAbstractItemView.MultiSelection
        )
        self.kind_list.setUniformItemSizes(True)
        self.kind_list.itemSelectionChanged.connect(
            self._on_kind_selection_changed
        )
        kind_layout.addWidget(self.kind_list, 1)

        select_all_btn = QPushButton("Выбрать все")
        select_all_btn.clicked.connect(self.kind_list.selectAll)
        kind_layout.addWidget(select_all_btn)

        clear_all_btn = QPushButton("Снять все")
        clear_all_btn.clicked.connect(self.kind_list.clearSelection)
        kind_layout.addWidget(clear_all_btn)

        splitter.addWidget(self.kind_panel)

        self.list_view = QListView()
        self.list_view.setModel(self.proxy)
        self.list_view.setViewMode(QListView.IconMode)
        self.list_view.setResizeMode(QListView.Adjust)
        self.list_view.setMovement(QListView.Static)
        self.list_view.setIconSize(QSize(GALLERY_ICON_SIZE, GALLERY_ICON_SIZE))
        self.list_view.setGridSize(QSize(128, 146))
        self.list_view.setWordWrap(True)
        self.list_view.setUniformItemSizes(False)
        self.list_view.setSpacing(4)
        self.list_view.setSelectionMode(
            QAbstractItemView.SingleSelection
        )
        self.list_view.selectionModel().currentChanged.connect(
            self._on_current_changed
        )
        splitter.addWidget(self.list_view)

        details = QWidget()
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(8, 0, 0, 0)

        header = QHBoxLayout()

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(190, 190)
        self.icon_label.setAlignment(Qt.AlignCenter)
        header.addWidget(self.icon_label, 0, Qt.AlignTop)

        text_block = QVBoxLayout()
        self.section_label = QLabel("—")
        self.section_label.setStyleSheet("font-weight: 600;")
        self.section_label.setWordWrap(True)
        text_block.addWidget(self.section_label)

        self.name_label = QLabel()
        self.name_label.setWordWrap(True)
        text_block.addWidget(self.name_label)

        self.meta_label = QLabel()
        self.meta_label.setWordWrap(True)
        self.meta_label.setProperty("role", "muted")
        text_block.addWidget(self.meta_label)

        text_block.addStretch(1)
        header.addLayout(text_block, 1)
        details_layout.addLayout(header)

        # Описание — отдельная прокручиваемая область: текст из
        # text/<lang>, а не ключ локализации из inv_name/description.
        self.description_panel = QWidget()
        description_layout = QVBoxLayout(self.description_panel)
        description_layout.setContentsMargins(0, 0, 0, 0)
        description_layout.setSpacing(2)

        self.description_caption = QLabel("Описание")
        self.description_caption.setProperty("role", "muted")
        description_layout.addWidget(self.description_caption)

        self.description_label = QLabel()
        self.description_label.setWordWrap(True)
        self.description_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.description_label.setTextInteractionFlags(
            Qt.TextSelectableByMouse
        )

        self.description_scroll = QScrollArea()
        self.description_scroll.setWidgetResizable(True)
        self.description_scroll.setFrameShape(QFrame.NoFrame)
        self.description_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff
        )
        self.description_scroll.setWidget(self.description_label)
        description_layout.addWidget(self.description_scroll, 1)

        self.fields_table = QTableWidget(0, 2)
        self.fields_table.setHorizontalHeaderLabels(["Поле", "Значение"])
        self.fields_table.verticalHeader().setVisible(False)
        self.fields_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.fields_table.setSelectionBehavior(
            QAbstractItemView.SelectRows
        )
        self.fields_table.setAlternatingRowColors(True)
        self.fields_table.setWordWrap(True)

        header_view = self.fields_table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.Stretch)

        # Один вертикальный захват между описанием и свойствами.
        self.details_splitter = QSplitter(Qt.Vertical)
        self.details_splitter.setChildrenCollapsible(False)
        self.details_splitter.setHandleWidth(6)
        self.details_splitter.addWidget(self.description_panel)
        self.details_splitter.addWidget(self.fields_table)
        self.details_splitter.setStretchFactor(0, 0)
        self.details_splitter.setStretchFactor(1, 1)
        self.details_splitter.setSizes([110, 450])

        border = self._palette("border", "#3C3F41")
        accent = self._palette("accent", "#3592E4")
        self.details_splitter.setStyleSheet(
            "QSplitter::handle:vertical { height: 6px; "
            "background-color: %s; }"
            "QSplitter::handle:vertical:hover { "
            "background-color: %s; }" % (border, accent)
        )

        self.description_panel.setVisible(False)
        details_layout.addWidget(self.details_splitter, 1)
        splitter.addWidget(details)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 0)
        splitter.setStretchFactor(2, 1)
        splitter.setSizes([180, 300, 700])
        layout.addWidget(splitter, 1)

        # -------------------------------------------------- кнопки
        buttons = QHBoxLayout()

        refresh_btn = QPushButton("Обновить")
        refresh_btn.clicked.connect(self._refresh)
        buttons.addWidget(refresh_btn)

        buttons.addStretch(1)

        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.hide)
        buttons.addWidget(close_btn)

        layout.addLayout(buttons)

    # -------------------------------------------------- наполнение
    def set_catalog(self, catalog, images):
        """Полностью пересобрать модель под новый каталог."""
        self._catalog = catalog
        self._images = images or {}

        source = (
            catalog.source_path.name
            if catalog.source_path is not None
            else "не найден"
        )
        info = "Источник: %s • предметов: %d" % (source, len(catalog.items))
        if catalog.warnings:
            info += "\n" + "\n".join(catalog.warnings)
        self.info_label.setText(info)

        self._filter_timer.stop()
        self._updating = True
        self.list_view.setUpdatesEnabled(False)

        self.model.clear()
        self._entries = list(catalog.items)
        self._entries.sort(key=lambda item: item.section.lower())

        # Список kind: обычные строки, выбранные подсвечиваются темой.
        kinds = sorted(
            {
                (entry.field("kind") or "").strip()
                for entry in self._entries
            },
            key=lambda kind: (kind == "", kind.lower()),
        )

        self.kind_list.blockSignals(True)
        self.kind_list.clear()
        for kind in kinds:
            item = QListWidgetItem(kind or "(без kind)")
            item.setData(Qt.UserRole, kind)
            self.kind_list.addItem(item)
        self.kind_list.selectAll()
        self.kind_list.blockSignals(False)

        for entry in self._entries:
            image = self._images.get(entry.section)
            pixmap = self._gallery_pixmap(image)

            item = QStandardItem(QIcon(pixmap), self._item_label(entry))
            item.setEditable(False)
            item.setData(entry, ENTRY_ROLE)
            item.setData(self._search_text(entry), SEARCH_ROLE)
            item.setData((entry.field("kind") or "").strip(), KIND_ROLE)
            item.setToolTip(self._entry_tooltip(entry))
            self.model.appendRow(item)

        self.proxy.set_filters(
            self.filter_edit.text(),
            self._selected_kinds(),
        )
        self.list_view.setUpdatesEnabled(True)
        self._updating = False

        self._current_entry = None
        self._clear_details()
        self._select_first_visible()

    def _first_entry(self):
        if self.proxy.rowCount() <= 0:
            return None
        return self._entry_from_index(self.proxy.index(0, 0))

    def _select_first_visible(self):
        entry = self._first_entry()
        if entry is None:
            self._clear_details()
            return

        was_updating = self._updating
        self._updating = True
        self.list_view.setCurrentIndex(self.proxy.index(0, 0))
        self._updating = was_updating

        self._current_entry = entry
        self._show_details(entry)

    def _entry_from_index(self, index):
        if index is None or not index.isValid():
            return None
        source = self.proxy.mapToSource(index)
        item = self.model.itemFromIndex(source)
        if item is None:
            return None
        return item.data(ENTRY_ROLE)

    def _gallery_pixmap(self, image):
        if image is None or image.isNull():
            return self._placeholder

        pixmap = QPixmap.fromImage(image)
        if (
            pixmap.width() > GALLERY_ICON_SIZE
            or pixmap.height() > GALLERY_ICON_SIZE
        ):
            pixmap = pixmap.scaled(
                GALLERY_ICON_SIZE,
                GALLERY_ICON_SIZE,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        return pixmap

    def _detail_pixmap(self, image):
        if image is None or image.isNull():
            return self._placeholder

        pixmap = QPixmap.fromImage(image)
        if pixmap.width() > 170 or pixmap.height() > 170:
            pixmap = pixmap.scaled(
                170,
                170,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        return pixmap

    @staticmethod
    def _item_label(entry):
        """Короткая сводка полей прямо в списке предметов."""
        parts = []
        kind = entry.field("kind")
        weight = entry.field("inv_weight")
        cost = entry.field("cost")
        if kind:
            parts.append(str(kind))
        if weight:
            parts.append("вес %s" % weight)
        if cost:
            parts.append("цена %s" % cost)

        if not parts:
            return entry.section
        return "%s\n%s" % (entry.section, " • ".join(parts))

    def _entry_tooltip(self, entry):
        parts = [entry.section]
        if entry.name_text:
            parts.append(entry.name_text)
        icon_texture = entry.field("icons_texture") or entry.icon_texture
        parts.append("icons_texture: %s" % icon_texture)
        parts.append(
            "inv_grid: x=%d, y=%d, %d×%d"
            % (
                entry.icon_x,
                entry.icon_y,
                entry.icon_width,
                entry.icon_height,
            )
        )
        if entry.icon_path is None:
            parts.append("Текстура не найдена")
        else:
            parts.append(str(entry.icon_path))
        return "\n".join(parts)

    @staticmethod
    def _search_text(entry):
        """Строка для прокси-фильтра: секция и основные поля."""
        return " ".join(
            [
                entry.section,
                entry.field("kind") or "",
                entry.field("inv_name") or "",
                entry.field("description") or "",
                entry.field("$spawn") or "",
            ]
        ).lower()

    # -------------------------------------------------- выбор
    def _on_current_changed(self, current, _previous):
        if self._updating:
            return

        entry = self._entry_from_index(current)
        if entry is None:
            self._current_entry = None
            self._clear_details()
            return

        if entry is self._current_entry:
            return

        self._current_entry = entry
        self._show_details(entry)

    def _show_details(self, entry):
        image = self._images.get(entry.section)
        self.icon_label.setPixmap(self._detail_pixmap(image))

        self.section_label.setText("[%s]" % entry.section)

        name = entry.name_text or entry.field("inv_name") or entry.section
        description = (
            entry.description_text
            or entry.field("description")
            or ""
        )
        self.name_label.setText(name)
        self.name_label.setToolTip(name)

        has_description = bool(description.strip())
        self.description_panel.setVisible(has_description)
        self.description_label.setText(description)

        kind = entry.field("kind") or "—"
        texture = entry.field("icons_texture") or entry.icon_texture
        texture_path = (
            str(entry.icon_path) if entry.icon_path is not None else "не найдена"
        )
        meta = (
            "kind: %s\n"
            "Сетка: x=%d, y=%d; %d×%d (%.0f×%.0f px)\n"
            "Текстура: %s\n%s"
            % (
                kind,
                entry.icon_x,
                entry.icon_y,
                entry.icon_width,
                entry.icon_height,
                entry.icon_width * GRID_SIZE,
                entry.icon_height * GRID_SIZE,
                texture,
                texture_path,
            )
        )
        self.meta_label.setText(meta)

        self.fields_table.setUpdatesEnabled(False)
        self.fields_table.clearContents()
        self.fields_table.setRowCount(len(entry.fields))

        for row, field in enumerate(entry.fields):
            key_item = QTableWidgetItem(field.key)
            value_item = QTableWidgetItem(field.value)
            value_item.setToolTip(field.value)
            self.fields_table.setItem(row, 0, key_item)
            self.fields_table.setItem(row, 1, value_item)

        self.fields_table.resizeRowsToContents()
        self.fields_table.setUpdatesEnabled(True)

    def _clear_details(self):
        self.icon_label.clear()
        self.section_label.setText("—")
        self.name_label.clear()
        self.meta_label.clear()
        self.description_label.clear()
        self.description_panel.setVisible(False)
        self.fields_table.clearContents()
        self.fields_table.setRowCount(0)

    # -------------------------------------------------- фильтр / кнопки
    def _schedule_filter(self, _text):
        """Не фильтровать на каждый символ: один проход после паузы."""
        self._filter_timer.start()

    def _selected_kinds(self):
        """Выбранные kind или None, если список kind пуст."""
        if self.kind_list.count() == 0:
            return None
        return {
            str(item.data(Qt.UserRole) or "")
            for item in self.kind_list.selectedItems()
        }

    def _on_kind_selection_changed(self):
        if self._updating:
            return
        self._filter_timer.stop()
        self._apply_filter_now()

    def _apply_filter_now(self):
        previous = self._current_entry

        self._updating = True
        self.list_view.setUpdatesEnabled(False)
        try:
            self.proxy.set_filters(
                self.filter_edit.text(),
                self._selected_kinds(),
            )
            entry = self._restore_or_first_entry(previous)
        finally:
            self.list_view.setUpdatesEnabled(True)
            self._updating = False

        if entry is None:
            self._current_entry = None
            self._clear_details()
            return

        if entry is not previous:
            self._current_entry = entry
            self._show_details(entry)

    def _restore_or_first_entry(self, previous):
        """Сохранить выбранный предмет, если он прошёл фильтр."""
        if previous is not None:
            section = previous.section
            for row in range(self.proxy.rowCount()):
                index = self.proxy.index(row, 0)
                entry = self._entry_from_index(index)
                if entry is not None and entry.section == section:
                    self.list_view.setCurrentIndex(index)
                    return entry

        entry = self._first_entry()
        if entry is not None:
            self.list_view.setCurrentIndex(self.proxy.index(0, 0))
        return entry

    def _refresh(self):
        if self._on_refresh is not None:
            self._on_refresh()

    def _palette(self, name, fallback=None):
        try:
            value = self._theme_color(name, fallback)
        except Exception:
            value = fallback
        return value or fallback or "#787878"
