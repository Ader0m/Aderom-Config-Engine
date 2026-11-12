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

import os
import re
from dataclasses import replace
from pathlib import Path

from sdk import (
    DEFAULT_SEARCH_DELAY_MS,
    ITEM_SECTION_MIME,
    ResolvedLineEdit,
    encode_item_section,
)
from sdk.events import Events

from PyQt5.QtCore import (
    QEvent,
    QMimeData,
    QPoint,
    QRect,
    QSize,
    QSortFilterProxyModel,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt5.QtGui import (
    QColor,
    QDrag,
    QIcon,
    QImage,
    QKeySequence,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QStandardItem,
    QStandardItemModel,
)
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
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


class ItemGalleryView(QListView):
    """Галерея, из которой предмет можно перетащить другим модулям."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_start = None
        self._drag_index = None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start = event.pos()
            self._drag_index = self.indexAt(event.pos())
        else:
            self._drag_start = None
            self._drag_index = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._drag_start is not None
            and self._drag_index is not None
            and self._drag_index.isValid()
            and event.buttons() & Qt.LeftButton
            and (event.pos() - self._drag_start).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            index = self._drag_index
            self._drag_start = None
            self._drag_index = None
            self._start_drag(index)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def startDrag(self, supportedActions):
        self._start_drag(self.currentIndex())

    def _start_drag(self, index):
        if index is None or not index.isValid():
            return

        entry = index.data(ENTRY_ROLE)
        if entry is None:
            return

        mime = QMimeData()
        mime.setData(
            ITEM_SECTION_MIME,
            encode_item_section(entry.section, entry.fields),
        )
        mime.setText(entry.section)

        drag = QDrag(self)
        drag.setMimeData(mime)

        icon = index.data(Qt.DecorationRole)
        if isinstance(icon, QIcon):
            pixmap = icon.pixmap(96, 96)
            if not pixmap.isNull():
                drag.setPixmap(pixmap)
                drag.setHotSpot(
                    QPoint(pixmap.width() // 2, pixmap.height() // 2)
                )

        drag.exec_(Qt.CopyAction)


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


def _project_tag(name) -> str:
    """Безопасный тег проекта для имён файлов и ключей локализации."""
    value = str(name or "").strip().lower()
    value = re.sub(r"[^\w.-]+", "_", value, flags=re.UNICODE)
    return value.strip("._-") or "project"


def _next_localization_base(base_key: str, existing_ids) -> str:
    """Свободный префикс ключей локализации для нового предмета.

    Первый предмет проекта получает ``st_ace_<проект>``; следующим
    добавляется числовой id: ``st_ace_<проект>_2`` и т.д.
    """
    existing = {str(value).lower() for value in existing_ids or ()}

    if (
        (base_key + "_name").lower() not in existing
        and (base_key + "_descr").lower() not in existing
    ):
        return base_key

    index = 2
    while True:
        candidate = "%s_%d" % (base_key, index)
        if (
            (candidate + "_name").lower() not in existing
            and (candidate + "_descr").lower() not in existing
        ):
            return candidate
        index += 1


def _build_item_section(
    section: str,
    parent,
    parent_fields,
    name_key: str,
    description_key: str,
    newline: str = "\n",
) -> str:
    """Текст новой секции предмета с пустыми полями родителя."""
    header = "[%s]" % section
    parent = str(parent or "").strip()
    if parent:
        header += ":%s" % parent

    lines = [header]
    seen = set()

    for key in parent_fields or ():
        key = str(key or "").strip()
        if not key:
            continue
        lower = key.lower()
        if lower in ("inv_name", "inv_name_short", "description"):
            continue
        if lower in seen:
            continue
        seen.add(lower)
        lines.append("%s =" % key)

    # Локализация нового предмета. У inv_name_short всегда тот же ключ,
    # что и у inv_name.
    lines.append("inv_name = %s" % name_key)
    lines.append("inv_name_short = %s" % name_key)
    lines.append("description = %s" % description_key)

    return newline.join(lines)


def _append_item_section(text: str, block: str, newline: str = "\n") -> str:
    text = str(text or "")
    if not text.strip():
        return block + newline
    return text.rstrip() + newline + newline + block + newline


def _xml_string_entries(
    name_key: str,
    description_key: str,
    newline: str = "\n",
) -> str:
    lines = []
    for key in (name_key, description_key):
        lines.extend((
            '\t<string id="%s">' % key,
            "\t\t<text></text>",
            "\t</string>",
        ))
    return newline.join(lines)


def _append_xml_strings(
    text: str,
    name_key: str,
    description_key: str,
    newline: str = "\n",
) -> str:
    block = _xml_string_entries(name_key, description_key, newline)
    text = str(text or "")

    if not text.strip():
        return newline.join((
            '<?xml version="1.0" encoding="windows-1251"?>',
            "",
            "<string_table>",
            block,
            "</string_table>",
            "",
        ))

    marker = "</string_table>"
    index = text.lower().rfind(marker)
    if index == -1:
        return text.rstrip() + newline + newline + block + newline

    return text[:index].rstrip() + newline + block + newline + text[index:]


def _ltx_header_name(line):
    value = str(line or "").strip()
    for prefix in ("!!", "!", "@"):
        if value.startswith(prefix + "["):
            value = value[len(prefix):]
            break
    if not value.startswith("["):
        return None
    close = value.find("]")
    if close == -1:
        return None
    return value[1:close].strip().lower()


def _ltx_section_at_line(text, line_number):
    lines = str(text or "").split("\n")
    index = min(max(int(line_number) - 1, 0), len(lines) - 1)
    for current in range(index, -1, -1):
        name = _ltx_header_name(lines[current])
        if name:
            return name
    return None


def _ltx_key_from_line(line):
    value = str(line or "").strip()
    if value.startswith("!") or value.startswith(">") or value.startswith("<"):
        value = value[1:].lstrip()
    if "=" in value:
        value = value.split("=", 1)[0]
    return value.strip().lower()


def _ltx_section_bounds(lines, section):
    target = str(section or "").strip().lower()
    start = None

    for index, line in enumerate(lines):
        name = _ltx_header_name(line)
        if name is None:
            continue
        if start is not None:
            return start, index
        if name == target:
            start = index

    if start is not None:
        return start, len(lines)
    return None


def _edit_section_text(text, section, key, action, new_line=None,
                       after_key=None):
    """Точечная правка секции в тексте файла-черновика."""
    original = str(text or "")
    newline = "\r\n" if "\r\n" in original else "\n"
    lines = original.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    bounds = _ltx_section_bounds(lines, section)
    if bounds is None:
        return None

    start, end = bounds
    key_low = str(key or "").strip().lower()
    found = None
    for index in range(start + 1, end):
        if _ltx_key_from_line(lines[index]) == key_low:
            found = index
            break

    if action == "replace":
        line = str(new_line or "").rstrip("\r\n")
        if found is None:
            lines.insert(end, line)
        else:
            lines[found] = line
    elif action == "delete":
        if found is None:
            return None
        del lines[found]
    elif action == "insert":
        line = str(new_line or "").rstrip("\r\n")
        if found is None:
            lines.insert(end, line)
        else:
            lines.insert(found + 1, line)
    else:
        return None

    return newline.join(lines)


def _declared_sections(text: str):
    """Имена секций LTX без учёта типа заголовка."""
    return {
        match.group(1).strip().lower()
        for match in re.finditer(
            r"^[ \t]*(?:!!|!|@)?\[([^\]]+)\]",
            str(text or ""),
            flags=re.MULTILINE,
        )
    }


class ParentComboBox(QComboBox):
    """Поле родителя, открывающее собственный список предложений."""

    popup_requested = pyqtSignal()

    def showPopup(self):
        # Не показываем стандартный popup: вместо него диалог открывает
        # прокручиваемый контейнер с ближайшими родителями.
        self.popup_requested.emit()


class ParentSuggestionPopup(QFrame):
    """Прокручиваемый список ближайших родителей под полем ввода."""

    suggestion_chosen = pyqtSignal(str)

    def __init__(self, parent=None):
        flags = (
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowDoesNotAcceptFocus
            | Qt.NoDropShadowWindowHint
        )
        super().__init__(parent, flags)
        self.setObjectName("parentSuggestionPopup")
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)

        palette = self.palette()
        background = palette.color(QPalette.Window).name()
        border = palette.color(QPalette.Mid).name()
        self.setStyleSheet(
            "#parentSuggestionPopup {"
            "  background-color: %s;"
            "  border: 1px solid %s;"
            "}" % (background, border)
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.list = QListWidget()
        self.list.setFocusPolicy(Qt.NoFocus)
        self.list.setUniformItemSizes(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.list.itemClicked.connect(self._on_item_clicked)
        self.list.itemActivated.connect(self._on_item_clicked)
        layout.addWidget(self.list)

    def set_suggestions(self, values):
        self.list.clear()
        for value in values or ():
            self.list.addItem(str(value))
        if self.list.count() > 0:
            self.list.setCurrentRow(0)
        return self.list.count()

    def show_at(self, widget):
        if self.list.count() <= 0:
            self.hide()
            return

        row_height = self.list.sizeHintForRow(0)
        if row_height <= 0:
            row_height = 22

        visible_rows = min(self.list.count(), 12)
        height = min(320, row_height * visible_rows + 4)
        width = max(widget.width(), 360)

        position = widget.mapToGlobal(
            QPoint(0, widget.height() + 2)
        )
        screen = QApplication.desktop().availableGeometry(widget)
        if position.y() + height > screen.bottom():
            position.setY(
                widget.mapToGlobal(QPoint(0, 0)).y() - height
            )

        self.setGeometry(QRect(position, QSize(width, height)))
        self.show()
        self.raise_()

    def _on_item_clicked(self, item):
        if item is None:
            return
        value = item.text()
        self.hide()
        self.suggestion_chosen.emit(value)


class CreateItemDialog(QDialog):
    """Выбор имени нового предмета и его родителя."""

    def __init__(self, parent_candidates=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle("Создать предмет")
        self.resize(560, 240)

        self._all_parents = sorted(
            {
                str(value).strip()
                for value in (parent_candidates or ())
                if str(value).strip()
            },
            key=str.lower,
        )
        self._choosing_parent = False

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Имя секции предмета:"))
        self.section_edit = QLineEdit()
        self.section_edit.setPlaceholderText(
            "например, my_custom_medkit"
        )
        layout.addWidget(self.section_edit)

        self.no_parent = QCheckBox("Нет родителя")
        layout.addWidget(self.no_parent)

        layout.addWidget(QLabel("Родитель:"))
        self.parent_combo = ParentComboBox()
        self.parent_combo.setEditable(True)
        self.parent_combo.setInsertPolicy(QComboBox.NoInsert)
        self.parent_combo.addItems(self._all_parents)
        self.parent_combo.setMinimumContentsLength(28)
        self.parent_combo.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon
        )

        line_edit = self.parent_combo.lineEdit()
        if line_edit is not None:
            line_edit.setPlaceholderText(
                "Выберите из списка или введите вручную"
            )
            line_edit.textEdited.connect(self._on_parent_text_edited)
            line_edit.installEventFilter(self)

        completer = self.parent_combo.completer()
        if completer is not None:
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            completer.setFilterMode(Qt.MatchContains)
            # Видимый список рисует наш прокручиваемый контейнер;
            # автодополнение остаётся строчным, без второго popup.
            completer.setCompletionMode(QCompleter.InlineCompletion)

        self._suggestion_popup = ParentSuggestionPopup(self)
        self._suggestion_popup.suggestion_chosen.connect(
            self._on_suggestion_chosen
        )
        self.parent_combo.popup_requested.connect(
            self._on_parent_popup_requested
        )
        layout.addWidget(self.parent_combo)

        hint = QLabel(
            "Все поля родителя будут добавлены в новую секцию "
            "с пустыми значениями. Родителя можно ввести вручную."
        )
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        layout.addWidget(hint)

        self.no_parent.toggled.connect(self._on_no_parent_toggled)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.button(QDialogButtonBox.Ok).setText("Создать")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _filter_parents(self, query):
        query = str(query or "").strip().lower()
        if not query:
            return list(self._all_parents)

        starts = []
        contains = []
        for value in self._all_parents:
            lowered = value.lower()
            if lowered.startswith(query):
                starts.append(value)
            elif query in lowered:
                contains.append(value)
        return starts + contains

    def _show_suggestions(self, query):
        if self.no_parent.isChecked():
            self._suggestion_popup.hide()
            return

        count = self._suggestion_popup.set_suggestions(
            self._filter_parents(query)
        )
        if count <= 0:
            self._suggestion_popup.hide()
            return
        self._suggestion_popup.show_at(self.parent_combo)

    def _on_parent_text_edited(self, text):
        if self._choosing_parent:
            return
        self._show_suggestions(text)

    def _on_parent_popup_requested(self):
        self._show_suggestions(self.parent_combo.currentText())

    def _on_suggestion_chosen(self, value):
        self._choosing_parent = True
        try:
            self.parent_combo.setCurrentText(value)
            line_edit = self.parent_combo.lineEdit()
            if line_edit is not None:
                line_edit.setText(value)
                line_edit.setFocus()
        finally:
            self._choosing_parent = False
        self._suggestion_popup.hide()

    def _hide_suggestions(self):
        self._suggestion_popup.hide()

    def _hide_suggestions_later(self):
        QTimer.singleShot(100, self._hide_suggestions_if_left)

    def _hide_suggestions_if_left(self):
        line_edit = self.parent_combo.lineEdit()
        if line_edit is not None and line_edit.hasFocus():
            return
        if self._suggestion_popup.list.underMouse():
            return
        self._suggestion_popup.hide()

    def eventFilter(self, obj, event):
        line_edit = self.parent_combo.lineEdit()
        if obj is line_edit:
            if event.type() == QEvent.FocusIn:
                self._show_suggestions(
                    self.parent_combo.currentText()
                )
            elif event.type() == QEvent.FocusOut:
                self._hide_suggestions_later()
        return super().eventFilter(obj, event)

    def hideEvent(self, event):
        self._suggestion_popup.hide()
        super().hideEvent(event)

    def moveEvent(self, event):
        self._suggestion_popup.hide()
        super().moveEvent(event)

    def _on_no_parent_toggled(self, checked):
        self.parent_combo.setEnabled(not checked)
        if checked:
            self._suggestion_popup.hide()

    def values(self):
        section = self.section_edit.text().strip()
        if self.no_parent.isChecked():
            return section, None
        parent = self.parent_combo.currentText().strip()
        return section, (parent or None)

    def accept(self):
        section = self.section_edit.text().strip()
        if not section:
            QMessageBox.warning(
                self,
                "Создать предмет",
                "Введите имя секции предмета.",
            )
            return

        if re.search(r"[\[\]:\r\n]", section):
            QMessageBox.warning(
                self,
                "Создать предмет",
                "Имя секции не должно содержать [ ], : или перевод строки.",
            )
            return

        if not self.no_parent.isChecked():
            parent = self.parent_combo.currentText().strip()
            if not parent:
                QMessageBox.warning(
                    self,
                    "Создать предмет",
                    "Выберите родителя, впишите его вручную "
                    "или отметьте «Нет родителя».",
                )
                return

        self._suggestion_popup.hide()
        super().accept()

    def reject(self):
        self._suggestion_popup.hide()
        super().reject()


class ItemViewerFeature:
    """Запрашивает каталог у ядра и показывает окно модуля."""

    WINDOW_KEY = WINDOW_KEY

    def __init__(self, ctx, session):
        self.ctx = ctx
        self.session = session
        self._window = None
        self._request_seq = 0
        self._running = False
        self._catalog = None

        # Контекст редактирования system.ltx (resolved-черновик проекта).
        self._root_path = None
        self._tab_id = ""
        self._title = ""
        self._resolved_text = ""
        self._original_text = ""
        self._line_sources = ()
        self._saved_text = ""

    def _project_overlay(self):
        fn = getattr(self.ctx.ui, "project_overlay", None)
        if not callable(fn):
            return None
        try:
            return fn()
        except Exception as exc:
            self.ctx.ui.log(
                "Слой правок проекта недоступен: %s" % exc
            )
            return None

    # -------------------------------------------------- жизненный цикл
    def show(self):
        if not self.session.has_tree:
            self.ctx.ui.show_error(
                "Дерево не построено: сначала «Пересобрать VFS»."
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
        overlay = self._project_overlay()
        has_overlay = bool(
            getattr(overlay, "source_files", None)
            or getattr(overlay, "resolved_edits", None)
        )

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
                            overlay=overlay,
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
                        reuse_snapshot_cache=not has_overlay,
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
        self._catalog = catalog
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
                on_create_item=self._create_item,
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

    @staticmethod
    def _same_path(left, right):
        try:
            return os.path.normcase(
                os.path.abspath(str(left))
            ) == os.path.normcase(os.path.abspath(str(right)))
        except (TypeError, ValueError, OSError):
            return False

    def _new_file_for_line(self, line_number):
        overlay = self._project_overlay()
        if overlay is None:
            return None

        new_files = {
            Path(path) for path in (overlay.new_files or ())
        }
        if not new_files:
            return None

        index = int(line_number) - 1
        if 0 <= index < len(self._line_sources):
            source = self._line_sources[index]
            path = getattr(source, "path", None)
            if path is not None:
                for candidate in new_files:
                    if self._same_path(candidate, path):
                        return candidate

        section = _ltx_section_at_line(self._resolved_text, line_number)
        if not section:
            return None

        for path, text in (overlay.source_files or {}).items():
            candidate = Path(path)
            if candidate not in new_files:
                continue
            lines = str(text or "").replace("\r\n", "\n").split("\n")
            if _ltx_section_bounds(lines, section) is not None:
                return candidate

        return None

    def _edit_new_item_file(self, line_number, action, new_line=None):
        path = self._new_file_for_line(line_number)
        if path is None:
            return False

        section = _ltx_section_at_line(self._resolved_text, line_number)
        if not section:
            return False

        lines = self._resolved_text.split("\n")
        index = int(line_number) - 1
        old_line = lines[index] if 0 <= index < len(lines) else ""
        key = _ltx_key_from_line(old_line)

        text = self.ctx.ui.read_project_text(path)
        updated = _edit_section_text(
            text,
            section,
            key,
            action,
            new_line=new_line,
        )
        if updated is None:
            self.ctx.ui.show_error(
                "Не удалось изменить секцию [%s] в %s"
                % (section, Path(path).name)
            )
            return False

        if not self.ctx.ui.write_project_text(path, updated):
            return False

        self.ctx.ui.log(
            "Проект: изменён новый файл %s (секция [%s])"
            % (Path(path).name, section)
        )
        self.ctx.ui.set_status(
            "Изменения записаны в %s" % Path(path).name,
            5000,
        )
        self.show()
        return True

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

        if self._new_file_for_line(line_number) is not None:
            return self._edit_new_item_file(
                line_number,
                "replace",
                new_line=new_line,
            )

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

        if self._new_file_for_line(line_number) is not None:
            return self._edit_new_item_file(line_number, "delete")

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

        if self._new_file_for_line(line_number) is not None:
            return self._edit_new_item_file(
                line_number,
                "insert",
                new_line=new_line,
            )

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

    # -------------------------------------------------- создание предмета
    def _parent_fields(self, parent):
        if not parent:
            return ()

        wanted = str(parent).strip().lower()
        catalog = self._catalog
        fields = []

        # Поля самой секции в том порядке, как они записаны в документе.
        for section in getattr(catalog, "sections", ()) or ():
            if section.name.lower() == wanted:
                fields.extend(section.fields or ())
                break

        # Эффективные поля предмета включают значения по умолчанию
        # (иконка и сетка), даже если ключ не записан явно.
        for entry in getattr(catalog, "items", ()) or ():
            if entry.section.lower() == wanted:
                fields.extend(field.key for field in entry.fields)
                break

        result = []
        seen = set()
        for key in fields:
            key = str(key or "").strip()
            if not key or key.lower() in seen:
                continue
            seen.add(key.lower())
            result.append(key)
        return tuple(result)

    def _create_item(self, section, parent):
        """Добавить новый предмет в файлы проекта."""
        if not self._can_edit():
            self.ctx.ui.show_error(
                "Создайте проект, чтобы добавлять предметы."
            )
            return False

        section = str(section or "").strip()
        parent = str(parent or "").strip() or None
        if not section:
            self.ctx.ui.show_error("Не задано имя секции предмета.")
            return False
        if re.search(r"[\[\]:\r\n]", section):
            self.ctx.ui.show_error(
                "Имя секции не должно содержать [ ], : или перевод строки."
            )
            return False

        forest = self.session.forest
        if forest is None:
            self.ctx.ui.show_error(
                "Дерево не построено: сначала «Пересобрать VFS»."
            )
            return False

        scan_dir = self.ctx.services.forest_scan_dir(forest)
        if scan_dir is None:
            self.ctx.ui.show_error(
                "Не удалось определить каталог gamedata\\configs."
            )
            return False

        project_name = self.ctx.ui.project_name() or "project"
        tag = _project_tag(project_name)

        items_dir = Path(scan_dir) / "items"
        text_dir = Path(scan_dir) / "text" / "rus"
        item_path = items_dir / ("mod_item_base_~ace_%s.ltx" % tag)
        xml_path = text_dir / ("st_ace_%s.xml" % tag)

        item_text = self.ctx.ui.read_project_text(item_path)
        xml_text = self.ctx.ui.read_project_text(xml_path)

        known_sections = _declared_sections(item_text)
        known_sections.update(
            section_info.name.lower()
            for section_info in (
                getattr(self._catalog, "sections", ()) or ()
            )
        )
        if section.lower() in known_sections:
            self.ctx.ui.show_error(
                "Секция [%s] уже существует." % section
            )
            return False

        parent_fields = self._parent_fields(parent)

        item_newline = "\r\n" if "\r\n" in item_text else "\n"
        xml_newline = "\r\n" if "\r\n" in xml_text else "\n"

        base_key = "st_ace_%s" % tag
        existing_ids = re.findall(
            r'<string\s+id\s*=\s*"([^"]+)"',
            xml_text,
            flags=re.IGNORECASE,
        )
        key_base = _next_localization_base(base_key, existing_ids)
        name_key = key_base + "_name"
        description_key = key_base + "_descr"

        section_block = _build_item_section(
            section,
            parent,
            parent_fields,
            name_key,
            description_key,
            newline=item_newline,
        )
        item_text = _append_item_section(
            item_text,
            section_block,
            newline=item_newline,
        )
        xml_text = _append_xml_strings(
            xml_text,
            name_key,
            description_key,
            newline=xml_newline,
        )

        if not self.ctx.ui.write_project_text(item_path, item_text):
            return False
        if not self.ctx.ui.write_project_text(xml_path, xml_text):
            return False

        self.ctx.ui.log(
            "Проект: создан предмет [%s] (%s, %s)"
            % (section, item_path.name, xml_path.name)
        )
        self.ctx.ui.set_status(
            "Предмет [%s] добавлен в проект" % section,
            6000,
        )
        self._register_virtual_files(
            (item_path, item_text),
            (xml_path, xml_text),
        )
        return True

    def _register_virtual_files(self, *files):
        """Добавить созданные черновики в текущее дерево VFS."""
        forest = self.session.forest
        add_file = getattr(
            self.ctx.services,
            "add_virtual_file",
            None,
        )
        if forest is None or not callable(add_file):
            return

        added = False
        for path, text in files:
            try:
                added = add_file(forest, path, text) or added
            except Exception as exc:
                self.ctx.ui.log(
                    "VFS: не удалось добавить %s: %s" % (path, exc)
                )

        if added:
            self.ctx.events.publish(Events.TREE_READY, forest)
        self.show()


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
        on_create_item=None,
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
        self._on_create_item = on_create_item

        self._catalog = None
        self._images = {}
        self._entries = []
        self._model_items = {}
        self._updating = False
        self._populating_fields = False
        self._current_entry = None

        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(DEFAULT_SEARCH_DELAY_MS)
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

        self.list_view = ItemGalleryView()
        self.list_view.setModel(self.proxy)
        self.list_view.setDragEnabled(True)
        self.list_view.setDragDropMode(QAbstractItemView.DragOnly)
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

        self.create_btn = QPushButton("Создать предмет")
        self.create_btn.clicked.connect(self._open_create_item_dialog)
        self.create_btn.setEnabled(self._on_create_item is not None)
        buttons.addWidget(self.create_btn)

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

    def _parent_candidates(self):
        """Родители, которые встречаются хотя бы в одной секции."""
        catalog = self._catalog
        candidates = {
            str(parent).strip()
            for parent in (getattr(catalog, "parent_names", ()) or ())
            if str(parent).strip()
        }

        # Резерв: если исходные файлы недоступны, используем токены
        # наследования из resolved-секций, а затем все имена секций.
        for section in getattr(catalog, "sections", ()) or ():
            candidates.update(
                str(parent).strip()
                for parent in (section.parents or ())
                if str(parent).strip()
            )

        if not candidates:
            candidates.update(
                section.name
                for section in (getattr(catalog, "sections", ()) or ())
                if section.name
            )

        return candidates

    def _open_create_item_dialog(self):
        if self._on_create_item is None:
            return

        dialog = CreateItemDialog(self._parent_candidates(), self)
        if not dialog.exec_():
            return

        section, parent = dialog.values()
        self._on_create_item(section, parent)

    def _palette(self, name, fallback=None):
        try:
            value = self._theme_color(name, fallback)
        except Exception:
            value = fallback
        return value or fallback or "#787878"
