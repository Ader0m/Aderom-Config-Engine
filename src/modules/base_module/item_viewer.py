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

from dataclasses import replace

from sdk import ResolvedLineEdit

from PyQt5.QtCore import QSize, QSortFilterProxyModel, Qt, QTimer
from PyQt5.QtGui import (
    QColor,
    QIcon,
    QImage,
    QKeySequence,
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
    QInputDialog,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QShortcut,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .ltx_open import resolved_tab_id

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

        # Контекст редактирования system.ltx (resolved-черновик проекта).
        self._root_path = None
        self._tab_id = ""
        self._title = ""
        self._resolved_text = ""
        self._original_text = ""
        self._line_sources = ()
        self._saved_text = ""

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

        root = self.ctx.services.item_catalog_root(forest)
        rel = (
            self.ctx.services.relative_path(forest, root)
            if root is not None
            else ""
        )
        tab_id = resolved_tab_id(rel) if rel else ""
        title = "RESOLVED: %s" % rel if rel else ""

        draft = self.ctx.ui.resolved_draft(tab_id) if tab_id else None

        self.ctx.ui.set_status("Предметы: сборка каталога…")
        self.ctx.ui.log("Просмотр игровых предметов: сборка каталога…")

        def work(log):
            original_text = ""
            sources = ()

            if root is not None:
                try:
                    original_text, sources = (
                        self.ctx.services.assemble_resolved(
                            root,
                            forest,
                            self.session.config_dir,
                            log=log,
                        )
                    )
                except Exception as exc:
                    log("Предметы: резолв system.ltx недоступен: %s" % exc)

            if draft is not None:
                text = draft.text
                line_sources = tuple(
                    draft.line_sources or sources or ()
                )
                original = draft.original_text or original_text
                saved = draft.saved_text
                catalog = self.ctx.services.build_item_catalog(
                    forest,
                    log=log,
                    resolved_text=text,
                )
            else:
                text = original_text
                line_sources = tuple(sources or ())
                original = original_text
                saved = original_text
                if original_text:
                    catalog = self.ctx.services.build_item_catalog(
                        forest,
                        log=log,
                        resolved_text=original_text,
                        reuse_snapshot_cache=True,
                    )
                else:
                    catalog = self.ctx.services.build_item_catalog(
                        forest,
                        log=log,
                    )

            line_count = text.count("\n") + 1
            fitted_sources = list(line_sources or ())
            if len(fitted_sources) > line_count:
                fitted_sources = fitted_sources[:line_count]
            while len(fitted_sources) < line_count:
                fitted_sources.append(None)
            line_sources = tuple(fitted_sources)

            # Ядро декодирует иконки пакетно: кэш регионов + пул процессов.
            # Модуль только превращает готовые RGBA в QImage.
            icons = self.ctx.services.load_item_icons(catalog, log=log)
            images = {
                icon.section: _image_from_region(icon)
                for icon in icons
            }

            context = {
                "root_path": root,
                "tab_id": tab_id,
                "title": title,
                "text": text,
                "original_text": original,
                "saved_text": saved,
                "line_sources": line_sources,
            }
            return catalog, images, context

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

        catalog, images, context = result
        self._apply_context(context)

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
                can_edit=self._can_edit,
                on_replace_line=self._replace_line,
                on_delete_line=self._delete_line,
                on_insert_line=self._insert_line,
                on_go_to_line=self._go_to_line,
                on_save=self._save_now,
            )

        self._window.set_catalog(catalog, images)
        self.ctx.ui.show_module_window(self.WINDOW_KEY, self._window)

    # -------------------------------------------------- редактирование
    def _apply_context(self, context):
        self._root_path = context.get("root_path")
        self._tab_id = str(context.get("tab_id") or "")
        self._title = str(context.get("title") or "")
        self._resolved_text = str(context.get("text") or "")
        self._original_text = str(context.get("original_text") or "")
        self._saved_text = str(context.get("saved_text") or "")
        self._line_sources = tuple(context.get("line_sources") or ())

    def _can_edit(self):
        return bool(
            self.ctx.ui.is_project_active()
            and self._root_path is not None
            and self._tab_id
            and self._resolved_text
        )

    def _commit_text(self, new_text, line_sources, line_edit=None):
        if not self._can_edit():
            self.ctx.ui.show_error(
                "Создайте проект, чтобы изменять свойства предметов."
            )
            return False

        latest = self.ctx.ui.resolved_draft(self._tab_id)
        if latest is not None and latest.text != self._resolved_text:
            self.ctx.ui.show_error(
                "system.ltx уже изменён в другой вкладке. "
                "Нажмите «Обновить» в просмотре предметов и повторите правку."
            )
            return False

        try:
            ok = self.ctx.ui.update_resolved_draft(
                self._tab_id,
                self._root_path,
                self._title,
                self._original_text,
                new_text,
                line_sources,
                line_edit=line_edit,
            )
        except Exception as exc:
            self.ctx.ui.show_error(
                "Не удалось обновить черновик system.ltx:\n%s" % exc
            )
            return False

        if not ok:
            return False

        self._resolved_text = str(new_text)
        self._line_sources = tuple(line_sources or ())

        info = self.ctx.ui.resolved_draft(self._tab_id)
        if info is not None:
            self._saved_text = info.saved_text

        self.ctx.ui.set_status("Свойство изменено в system.ltx", 4000)
        return True

    def _replace_line(self, line_number, new_line):
        lines = self._resolved_text.split("\n")
        index = int(line_number) - 1
        if index < 0 or index >= len(lines):
            self.ctx.ui.show_error("Строка %s не найдена." % line_number)
            return False

        new_line = str(new_line).rstrip("\r\n")
        lines[index] = new_line
        return self._commit_text(
            "\n".join(lines),
            self._line_sources,
            line_edit=ResolvedLineEdit(
                action="replace",
                line_number=int(line_number),
                text=new_line,
            ),
        )

    def _delete_line(self, line_number):
        lines = self._resolved_text.split("\n")
        index = int(line_number) - 1
        if index < 0 or index >= len(lines):
            self.ctx.ui.show_error("Строка %s не найдена." % line_number)
            return False

        sources = list(self._line_sources)
        while len(sources) < len(lines):
            sources.append(None)

        del lines[index]
        if index < len(sources):
            del sources[index]

        return self._commit_text(
            "\n".join(lines),
            tuple(sources),
            line_edit=ResolvedLineEdit(
                action="delete",
                line_number=int(line_number),
            ),
        )

    def _insert_line(self, line_number, new_line):
        lines = self._resolved_text.split("\n")
        line_number = int(line_number)
        if line_number < 0 or line_number > len(lines):
            self.ctx.ui.show_error("Строка %s не найдена." % line_number)
            return False

        index = line_number
        line_count = len(lines)
        lines.insert(index, str(new_line).rstrip("\r\n"))

        sources = list(self._line_sources)
        while len(sources) < line_count:
            sources.append(None)
        sources.insert(index, None)

        return self._commit_text(
            "\n".join(lines),
            tuple(sources),
            line_edit=ResolvedLineEdit(
                action="insert",
                line_number=int(line_number),
                text=str(new_line).rstrip("\r\n"),
            ),
        )

    def _go_to_line(self, line_number):
        if self._root_path is None or not self._tab_id:
            self.ctx.ui.show_error(
                "Не удалось определить файл system.ltx."
            )
            return

        line_number = int(line_number)
        line_count = self._resolved_text.count("\n") + 1
        if line_number < 1 or line_number > line_count:
            return

        self.ctx.ui.open_resolved_tab(
            self._tab_id,
            self._title,
            self._resolved_text,
            self._line_sources,
            self._root_path,
            line_number=int(line_number),
        )

    def _save_now(self):
        saved = self.ctx.ui.save_project_now()
        if saved:
            self.ctx.ui.set_status(
                "Проект сохранён: %d файл(ов)" % saved,
                4000,
            )


class ItemViewerWindow(QDialog):
    """Немодальное окно: список предметов слева, поля и иконка справа."""

    def __init__(
        self,
        theme_color=None,
        on_refresh=None,
        can_edit=None,
        on_replace_line=None,
        on_delete_line=None,
        on_insert_line=None,
        on_go_to_line=None,
        on_save=None,
        parent=None,
    ):
        super().__init__(parent)

        self._theme_color = theme_color or (
            lambda name, fallback=None: fallback
        )
        self._on_refresh = on_refresh
        self._can_edit = can_edit or (lambda: False)
        self._on_replace_line = on_replace_line
        self._on_delete_line = on_delete_line
        self._on_insert_line = on_insert_line
        self._on_go_to_line = on_go_to_line
        self._on_save = on_save

        self._catalog = None
        self._images = {}
        self._entries = []
        self._model_items = {}
        self._updating = False
        self._populating_fields = False
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
        self.fields_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.fields_table.customContextMenuRequested.connect(
            self._on_fields_context_menu
        )
        self.fields_table.itemChanged.connect(
            self._on_field_item_changed
        )

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

        QShortcut(
            QKeySequence("Ctrl+S"),
            self,
            context=Qt.WidgetWithChildrenShortcut,
            activated=self._request_save,
        )

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
        self._model_items = {}
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
            self._model_items[entry.section] = item

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

        self._populating_fields = True
        self.fields_table.setUpdatesEnabled(False)
        self.fields_table.clearContents()
        self.fields_table.setRowCount(len(entry.fields))

        editable = bool(self._can_edit())
        any_editable = False

        for row, field in enumerate(entry.fields):
            key_item = QTableWidgetItem(field.key)
            value_item = QTableWidgetItem(field.value)
            value_item.setToolTip(field.value)

            can_edit_field = editable and field.line_number > 0
            any_editable = any_editable or can_edit_field
            for column, table_item in ((0, key_item), (1, value_item)):
                flags = table_item.flags()
                if can_edit_field:
                    flags |= Qt.ItemIsEditable
                else:
                    flags &= ~Qt.ItemIsEditable
                table_item.setFlags(flags)
                self.fields_table.setItem(row, column, table_item)

        self.fields_table.setEditTriggers(
            (
                QAbstractItemView.DoubleClicked
                | QAbstractItemView.EditKeyPressed
            )
            if any_editable
            else QAbstractItemView.NoEditTriggers
        )

        self.fields_table.resizeRowsToContents()
        self.fields_table.setUpdatesEnabled(True)
        self._populating_fields = False

    def _clear_details(self):
        self.icon_label.clear()
        self.section_label.setText("—")
        self.name_label.clear()
        self.meta_label.clear()
        self.description_label.clear()
        self.description_panel.setVisible(False)
        self.fields_table.clearContents()
        self.fields_table.setRowCount(0)

    def showEvent(self, event):
        super().showEvent(event)
        if self._current_entry is not None:
            self._show_details(self._current_entry)

    # -------------------------------------------------- правка system.ltx
    def _request_save(self):
        if self._on_save is not None:
            self._on_save()

    def _on_fields_context_menu(self, position):
        index = self.fields_table.indexAt(position)
        if not index.isValid():
            return

        entry = self._current_entry
        row = index.row()
        if entry is None or row < 0 or row >= len(entry.fields):
            return

        field = entry.fields[row]
        line = field.line_number or entry.line_number
        editable = bool(self._can_edit())

        menu = QMenu(self)

        go_action = menu.addAction("Перейти к строке")
        go_action.setEnabled(line > 0)

        add_action = menu.addAction("Добавить строку под")
        add_action.setEnabled(editable and line > 0)

        delete_action = menu.addAction("Удалить строку")
        delete_action.setEnabled(editable and field.line_number > 0)

        chosen = menu.exec_(
            self.fields_table.viewport().mapToGlobal(position)
        )

        if chosen is go_action and line > 0:
            self._go_to_line(line)
        elif chosen is add_action and line > 0:
            self._add_line_under(entry, field, line)
        elif chosen is delete_action and field.line_number > 0:
            self._delete_selected_line(entry, row, field)

    def _go_to_line(self, line_number):
        if self._on_go_to_line is not None:
            self._on_go_to_line(int(line_number))

    def _add_line_under(self, entry, field, line_number):
        text, ok = QInputDialog.getText(
            self,
            "Добавить строку под",
            "Строка LTX:",
            text="",
        )
        if not ok:
            return

        text = str(text).rstrip("\r\n")
        if not text.strip():
            return

        if "=" not in text:
            QMessageBox.warning(
                self,
                "Строка LTX",
                "Введите строку вида: ключ = значение",
            )
            return

        if self._on_insert_line is None:
            return
        if not self._on_insert_line(int(line_number), text):
            return

        self._apply_local_insert(entry, field, line_number, text)

    def _delete_selected_line(self, entry, row, field):
        if self._on_delete_line is None:
            return
        if not self._on_delete_line(int(field.line_number)):
            return

        self._apply_local_delete(entry, row, field)

    def _on_field_item_changed(self, item):
        if self._populating_fields or self._current_entry is None:
            return
        if not self._can_edit():
            return

        row = item.row()
        if row < 0 or row >= len(self._current_entry.fields):
            return

        field = self._current_entry.fields[row]
        if field.line_number <= 0:
            return

        key_item = self.fields_table.item(row, 0)
        value_item = self.fields_table.item(row, 1)
        if key_item is None or value_item is None:
            return

        new_key = key_item.text().strip()
        new_value = value_item.text().strip()
        if not new_key:
            self._show_details(self._current_entry)
            return

        if new_key == field.key and new_value == field.value:
            return

        new_line = "%s = %s" % (new_key, new_value)
        if self._on_replace_line is None:
            return
        if not self._on_replace_line(int(field.line_number), new_line):
            self._show_details(self._current_entry)
            return

        self._apply_local_replace(
            self._current_entry,
            row,
            new_key,
            new_value,
        )

    def _apply_local_replace(self, entry, row, new_key, new_value):
        fields = list(entry.fields)
        fields[row] = replace(
            fields[row],
            key=new_key,
            value=new_value,
        )

        updates = {}
        key_lower = new_key.lower()
        if key_lower == "inv_name":
            updates["name_text"] = new_value
        elif key_lower == "description":
            updates["description_text"] = new_value

        self._replace_entry(
            entry,
            replace(entry, fields=tuple(fields), **updates),
        )

    def _apply_local_delete(self, entry, row, field):
        fields = list(entry.fields)
        if row < len(fields):
            del fields[row]

        updates = {}
        key_lower = field.key.lower()
        if key_lower == "inv_name":
            updates["name_text"] = ""
        elif key_lower == "description":
            updates["description_text"] = ""

        new_entry = replace(entry, fields=tuple(fields), **updates)
        self._set_entry(new_entry)
        self._shift_lines(field.line_number, -1)
        self._show_details(self._current_entry)

    def _apply_local_insert(self, entry, field, line_number, text):
        key, value = self._parse_line(text)

        new_line_number = int(line_number) + 1
        self._shift_lines(line_number, +1)

        current = self._current_entry
        fields = list(current.fields)

        insert_at = len(fields)
        if line_number == current.line_number:
            insert_at = 0
        else:
            for index, candidate in enumerate(fields):
                if candidate.line_number == line_number:
                    insert_at = index + 1
                    break

        fields.insert(
            insert_at,
            replace(
                field,
                key=key,
                value=value,
                line_number=new_line_number,
            ),
        )

        updates = {}
        key_lower = key.lower()
        if key_lower == "inv_name":
            updates["name_text"] = value
        elif key_lower == "description":
            updates["description_text"] = value

        self._replace_entry(
            current,
            replace(current, fields=tuple(fields), **updates),
        )

    @staticmethod
    def _parse_line(text):
        if "=" in text:
            key, value = text.split("=", 1)
            return key.strip(), value.strip()
        return text.strip(), ""

    def _shift_lines(self, after_line, delta):
        """Сдвинуть номера строк во всех записях после правки."""
        section = (
            self._current_entry.section
            if self._current_entry is not None
            else None
        )

        for index, entry in enumerate(self._entries):
            fields = tuple(
                replace(
                    field,
                    line_number=(
                        field.line_number + delta
                        if field.line_number > after_line
                        else field.line_number
                    ),
                )
                for field in entry.fields
            )
            entry_line = entry.line_number
            if entry_line > after_line:
                entry_line += delta

            new_entry = replace(
                entry,
                fields=fields,
                line_number=entry_line,
            )
            self._entries[index] = new_entry

            model_item = self._model_items.get(entry.section)
            if model_item is not None:
                model_item.setData(new_entry, ENTRY_ROLE)

        if section is not None:
            self._current_entry = next(
                (
                    entry for entry in self._entries
                    if entry.section == section
                ),
                None,
            )

    def _replace_entry(self, old_entry, new_entry):
        for index, entry in enumerate(self._entries):
            if entry.section == old_entry.section:
                self._entries[index] = new_entry
                break

        model_item = self._model_items.get(new_entry.section)
        if model_item is not None:
            model_item.setData(new_entry, ENTRY_ROLE)
            model_item.setData(self._search_text(new_entry), SEARCH_ROLE)
            model_item.setData(
                (new_entry.field("kind") or "").strip(),
                KIND_ROLE,
            )
            model_item.setText(self._item_label(new_entry))
            model_item.setToolTip(self._entry_tooltip(new_entry))

        self.proxy.invalidateFilter()
        self._current_entry = new_entry
        self._show_details(new_entry)

    def _set_entry(self, new_entry):
        for index, entry in enumerate(self._entries):
            if entry.section == new_entry.section:
                self._entries[index] = new_entry
                break
        self._current_entry = new_entry

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
