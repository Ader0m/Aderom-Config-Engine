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

import inspect
import re
from pathlib import Path

from sdk import (
    DEFAULT_SEARCH_DELAY_MS,
    ITEM_SECTION_MIME,
    encode_item_section,
    engine_source_text,
    parse_color_definitions,
    parse_engine_text,
    path_key as sdk_path_key,
    same_path as sdk_same_path,
)
from sdk.events import Events

from PyQt5.QtCore import (
    QEvent,
    QMimeData,
    QModelIndex,
    QPoint,
    QRect,
    QRectF,
    QSize,
    QSortFilterProxyModel,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt5.QtGui import (
    QColor,
    QDrag,
    QFont,
    QFontMetrics,
    QIcon,
    QImage,
    QKeySequence,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QStandardItem,
    QStandardItemModel,
    QTextCharFormat,
    QTextCursor,
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
    QPlainTextEdit,
    QPushButton,
    QShortcut,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
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


def _call_with_overlay(func, *args, overlay=None, **kwargs):
    """Передать ``overlay`` только тем сервисам, которые его понимают."""
    if overlay is not None:
        try:
            parameters = inspect.signature(func).parameters
        except (TypeError, ValueError):
            parameters = {}

        accepts = "overlay" in parameters or any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        if accepts:
            kwargs["overlay"] = overlay
    return func(*args, **kwargs)


def _call_async_text_save(
    writer,
    path,
    text,
    on_done,
    original_text=None,
):
    """Вызвать асинхронную запись, передав необязательный original_text."""
    kwargs = {"on_done": on_done}
    if original_text is not None:
        try:
            parameters = inspect.signature(writer).parameters
        except (TypeError, ValueError):
            parameters = {}

        accepts = "original_text" in parameters or any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        if accepts:
            kwargs["original_text"] = original_text

    return bool(writer(path, text, **kwargs))


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
        self._images = {}

        # Контекст редактирования system.ltx. Текст, источники и dirty
        # принадлежат живому resolved-документу; модуль хранит только
        # идентификаторы и заголовок для представления.
        self._root_path = None
        self._tab_id = ""
        self._title = ""
        self._root_text = ""
        self._root_sources = ()
        # Цвета ``%c[name]`` из ``ui/color_defs.xml`` (с overlay проекта).
        self._color_defs_cache = None

        # Открытые карточки и удержанные документы.
        self._cards = {}
        self._card_retains = set()
        self._retained = set()

        # Фоновые сохранения текста: не более одной задачи на файл.
        self._text_save_jobs = {}
        self._text_save_seq = 0
        self._disposed = False

        self._pending_sections = set()
        self._pending_cards = set()
        self._pending_full = False
        self._pending_focus_line = 0
        self._refresh_timer = (
            QTimer() if QApplication.instance() is not None else None
        )
        if self._refresh_timer is not None:
            self._refresh_timer.setSingleShot(True)
            self._refresh_timer.setInterval(250)
            self._refresh_timer.timeout.connect(self._flush_refresh)

        events = getattr(ctx, "events", None)
        self._doc_sub = None
        self._tab_sub = None
        self._card_req_sub = None
        self._project_sub = None
        self._project_saved_sub = None
        if events is not None:
            self._doc_sub = events.subscribe(
                Events.RESOLVED_DOCUMENT_CHANGED,
                self._on_document_changed,
            )
            self._tab_sub = events.subscribe(
                Events.UI_TAB_CLOSED,
                self._on_tab_closed,
            )
            self._project_sub = events.subscribe(
                Events.PROJECT_CHANGED,
                self._on_project_changed,
                priority=-100,
            )
            self._project_saved_sub = events.subscribe(
                Events.PROJECT_SAVED,
                self._on_project_saved,
            )
            register_action = getattr(
                self.ctx.ui,
                "register_action_handler",
                None,
            )
            if callable(register_action):
                self._card_req_sub = register_action(
                    Events.ITEM_CARD_REQUESTED,
                    self._on_card_requested,
                )
            else:
                # Совместимость с минимальными тестовыми контекстами.
                self._card_req_sub = events.subscribe(
                    Events.ITEM_CARD_REQUESTED,
                    self._on_card_requested,
                )

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

    def _color_definitions(self):
        """Цвета ``%c[name]`` из ``configs/ui/color_defs.xml``."""
        if self._color_defs_cache is not None:
            return self._color_defs_cache

        session = self.session
        config_dir = getattr(session, "config_dir", None) if session else None
        if not config_dir and session is not None:
            config_dir = getattr(session.forest, "scan_dir", None)
        if not config_dir:
            self._color_defs_cache = {}
            return self._color_defs_cache

        path = Path(config_dir) / "ui" / "color_defs.xml"
        text = self._overlay_text(path)

        if text is None:
            services = getattr(self.ctx, "services", None)
            reader = getattr(services, "read_text", None)
            if callable(reader):
                try:
                    text = reader(path)
                except Exception:
                    text = None

        if text is None:
            try:
                raw = path.read_bytes()
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    text = raw.decode("windows-1251", errors="replace")
            except OSError:
                text = ""

        self._color_defs_cache = parse_color_definitions(text)
        return self._color_defs_cache

    def _overlay_text(self, path):
        overlay = self._project_overlay()
        sources = getattr(overlay, "source_files", None)
        if sources is None and isinstance(overlay, dict):
            sources = overlay
        if not sources:
            return None
        try:
            wanted = sdk_path_key(path)
            for source_path, source_text in sources.items():
                if sdk_path_key(source_path) == wanted:
                    return str(source_text or "")
        except Exception:
            return None
        return None

    # -------------------------------------------------- жизненный цикл
    def show(self):
        self._disposed = False
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
        self._color_defs_cache = None
        forest = self.session.forest

        root = self.ctx.services.item_catalog_root(forest)
        rel = (
            self.ctx.services.relative_path(forest, root)
            if root is not None
            else ""
        )
        tab_id = resolved_tab_id(rel) if rel else ""
        title = "[Res]%s" % rel if rel else ""

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
                catalog = _call_with_overlay(
                    self.ctx.services.build_item_catalog,
                    forest,
                    log=log,
                    resolved_text=text,
                    overlay=overlay,
                )
            else:
                text = original_text
                line_sources = tuple(sources or ())
                original = original_text
                saved = original_text
                if original_text:
                    catalog = _call_with_overlay(
                        self.ctx.services.build_item_catalog,
                        forest,
                        log=log,
                        resolved_text=original_text,
                        reuse_snapshot_cache=not has_overlay,
                        overlay=overlay,
                    )
                else:
                    catalog = _call_with_overlay(
                        self.ctx.services.build_item_catalog,
                        forest,
                        log=log,
                        overlay=overlay,
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
        self._disposed = True
        for job in list(self._text_save_jobs.values()):
            self._set_save_busy(job, False)
        self._text_save_jobs.clear()

        if self._window is not None:
            self.ctx.ui.close_module_window(self.WINDOW_KEY)
            self._window = None

        for tab_id in list(self._card_retains):
            card_tab_id = tab_id
            record = self._cards.get(card_tab_id)
            if record is not None:
                fn = getattr(
                    self.ctx.ui,
                    "release_resolved_document",
                    None,
                )
                if callable(fn):
                    fn(record[0])
        self._cards.clear()
        self._card_retains.clear()

        for tab_id in list(self._retained):
            fn = getattr(self.ctx.ui, "release_resolved_document", None)
            if callable(fn):
                fn(tab_id)
        self._retained.clear()

        for sub in (
            getattr(self, "_doc_sub", None),
            getattr(self, "_tab_sub", None),
            getattr(self, "_card_req_sub", None),
            getattr(self, "_project_sub", None),
            getattr(self, "_project_saved_sub", None),
        ):
            if sub is not None and hasattr(sub, "unsubscribe"):
                try:
                    sub.unsubscribe()
                except Exception:
                    pass
        self._doc_sub = None
        self._tab_sub = None
        self._card_req_sub = None

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
        self._images = images or {}
        previous_tab = self._tab_id
        self._apply_context(context)
        self._ensure_root_document(context, previous_tab)

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
                color_defs=self._color_definitions,
                on_refresh=self.show,
                can_edit=self._can_edit,
                on_replace_line=lambda line, text: self._replace_line(
                    self._tab_id,
                    line,
                    text,
                ),
                on_delete_line=lambda line: self._delete_line(
                    self._tab_id,
                    line,
                ),
                on_insert_line=lambda line, text: self._insert_line(
                    self._tab_id,
                    line,
                    text,
                ),
                on_field_edit=lambda section, key, value: self._apply_field(
                    self._tab_id,
                    section,
                    key,
                    value,
                ),
                on_field_rename=lambda section, old_key, new_key, value: (
                    self._rename_field(
                        self._tab_id,
                        section,
                        old_key,
                        new_key,
                        value,
                    )
                ),
                on_text_edit=lambda section, field, value: (
                    self._apply_item_text(
                        self._tab_id,
                        section,
                        field,
                        value,
                    )
                ),
                on_go_to_line=lambda line: self._go_to_line(
                    self._tab_id,
                    line,
                ),
                on_save=self._save_now,
                on_create_item=self._create_item,
                on_item_entry=lambda section: self._item_entry(
                    self._tab_id,
                    section,
                ),
                on_open_card=lambda section: self.open_card(
                    self._tab_id,
                    section,
                ),
                on_open_text_source=self._open_text_source,
                on_undo=lambda: self.ctx.ui.undo_resolved_document(
                    self._tab_id
                ),
                on_redo=lambda: self.ctx.ui.redo_resolved_document(
                    self._tab_id
                ),
                document_title=self._title,
            )

        self._window.set_catalog(catalog, images)
        self.ctx.ui.show_module_window(self.WINDOW_KEY, self._window)

    # -------------------------------------------------- редактирование
    def _apply_context(self, context):
        self._root_path = context.get("root_path")
        self._tab_id = str(context.get("tab_id") or "")
        self._title = str(context.get("title") or "")
        self._root_text = str(context.get("text") or "")
        self._root_sources = tuple(context.get("line_sources") or ())

    def _on_project_saved(self, _event):
        """Обновить список цветов после сохранения XML-файлов проекта."""
        self._color_defs_cache = None
        cards = []
        if self._window is not None:
            cards.append(getattr(self._window, "card", None))
        cards.extend(card for _tab, _section, card in self._cards.values())
        for card in cards:
            refresh = getattr(card, "refresh_color_suggestions", None)
            if callable(refresh):
                refresh()

    def _on_project_changed(self, _project):
        """Перепривязать корневой документ каталога к новому проекту.

        Платформа освобождает все живые resolved-документы при смене
        проекта. Окно и каталог при этом не пересобираются: карточки
        проверяют право на правку в момент действия, поэтому достаточно
        вернуть документ в реестр.
        """
        # Удержания относились к освобождённым документам.
        self._retained.clear()
        self._color_defs_cache = None
        if self._disposed or not self._tab_id or self._root_path is None:
            return
        if not self.ctx.ui.is_project_active():
            return
        if self._snapshot(self._tab_id) is not None:
            return
        self._ensure_root_document(
            {
                "root_path": self._root_path,
                "tab_id": self._tab_id,
                "title": self._title,
                "text": self._root_text,
                "line_sources": self._root_sources,
            },
            None,
        )

    # -------------------------------------------------- документ и карточки
    def _ensure_root_document(self, context, previous_tab):
        tab_id = str(context.get("tab_id") or "")
        root_path = context.get("root_path")
        if not tab_id or root_path is None:
            return
        ensure = getattr(self.ctx.ui, "ensure_resolved_document", None)
        if callable(ensure):
            ensure(
                tab_id,
                root_path,
                self._title,
                str(context.get("text") or ""),
                tuple(context.get("line_sources") or ()),
            )
        if previous_tab and previous_tab != tab_id:
            self._release(previous_tab)
        self._retain(tab_id)

    def _retain(self, tab_id):
        tab_id = str(tab_id or "")
        if not tab_id or tab_id in self._retained:
            return
        fn = getattr(self.ctx.ui, "retain_resolved_document", None)
        if callable(fn) and fn(tab_id):
            self._retained.add(tab_id)

    def _release(self, tab_id):
        tab_id = str(tab_id or "")
        if tab_id not in self._retained:
            return
        fn = getattr(self.ctx.ui, "release_resolved_document", None)
        if callable(fn):
            fn(tab_id)
        self._retained.discard(tab_id)

    def _item_entry(self, tab_id, section):
        fn = getattr(self.ctx.ui, "item_entry", None)
        if not callable(fn):
            return None
        try:
            return fn(tab_id, section)
        except Exception as exc:
            self.ctx.ui.log(
                "Предметы: не удалось получить карточку [%s]: %s"
                % (section, exc)
            )
            return None

    def _image(self, section):
        return self._images.get(section)

    def _document_title(self, tab_id):
        fn = getattr(self.ctx.ui, "resolved_document_title", None)
        if callable(fn):
            title = fn(tab_id)
            if title:
                return title
        if str(tab_id) == self._tab_id:
            return self._title
        return ""

    def _go_to_section(self, tab_id, section):
        entry = self._item_entry(tab_id, section)
        line = entry.line_number if entry is not None else None
        fn = getattr(self.ctx.ui, "open_resolved_document", None)
        if callable(fn):
            fn(tab_id, line)

    def _on_document_changed(self, change):
        tab_id = str(getattr(change, "tab_id", "") or "")
        if not tab_id:
            return

        sections = tuple(
            str(section).lower()
            for section in (getattr(change, "sections", ()) or ())
        )
        full = bool(getattr(change, "full", False))

        if tab_id == self._tab_id:
            focus_line = int(getattr(change, "focus_line", 0) or 0)
            if focus_line:
                self._pending_focus_line = focus_line
            if full:
                self._pending_full = True
            elif sections:
                self._pending_sections.update(sections)
            elif not focus_line:
                self._pending_full = True

        for card_tab_id, record in list(self._cards.items()):
            root_tab, section, _card = record
            if root_tab != tab_id:
                continue
            if full or not sections or section.lower() in sections:
                self._pending_cards.add(card_tab_id)

        if self._pending_sections or self._pending_full or self._pending_cards:
            if self._refresh_timer is not None:
                self._refresh_timer.start()
            else:
                self._flush_refresh()

    def _flush_refresh(self):
        full = self._pending_full
        sections = set(self._pending_sections)
        cards = set(self._pending_cards)
        focus_line = self._pending_focus_line

        self._pending_full = False
        self._pending_sections.clear()
        self._pending_cards.clear()
        self._pending_focus_line = 0

        if (
            not full
            and not sections
            and focus_line
            and self._tab_id
        ):
            section = self.ctx.ui.resolved_section_at_line(
                self._tab_id,
                focus_line,
            )
            if section:
                sections.add(section.lower())

        if self._window is not None and self._tab_id:
            if full:
                if self._window.isVisible():
                    self.show()
                else:
                    section = self._window.card.section()
                    if section:
                        self._window.update_section(
                            section,
                            self._item_entry(self._tab_id, section),
                        )
            else:
                for section in sections:
                    self._window.update_section(
                        section,
                        self._item_entry(self._tab_id, section),
                    )

        for card_tab_id in cards:
            record = self._cards.get(card_tab_id)
            if record is None:
                continue
            _root_tab, section, card = record
            if (
                not full
                and sections
                and section.lower() not in sections
            ):
                continue
            if not full and not sections:
                continue
            card.refresh()

    def _on_tab_closed(self, tab_id):
        record = self._cards.pop(str(tab_id), None)
        if record is None:
            return
        root_tab, _section, _card = record
        fn = getattr(self.ctx.ui, "release_resolved_document", None)
        if callable(fn):
            fn(root_tab)
        self._card_retains.discard(str(tab_id))

    def _on_card_requested(self, request):
        tab_id = str(getattr(request, "tab_id", "") or "")
        section = str(getattr(request, "section", "") or "")
        if not tab_id or not section:
            return False
        if self._item_entry(tab_id, section) is None:
            self.ctx.ui.set_status(
                "Секция [%s] не является предметом" % section,
                5000,
            )
            return True
        self.open_card(tab_id, section)
        return True

    @staticmethod
    def _card_tab_id(tab_id, section):
        return "%s#item:%s" % (
            str(tab_id),
            str(section).strip().lower(),
        )

    @staticmethod
    def _card_title(section):
        return "[item]%s" % str(section)

    def open_card(self, tab_id, section):
        """Открыть карточку предмета как вкладку основного окна."""
        section = str(section or "").strip()
        if not section:
            return

        card_tab_id = self._card_tab_id(tab_id, section)
        title = self._card_title(section)
        existing = self._cards.get(card_tab_id)
        if existing is not None:
            self.ctx.ui.open_editor_tab(
                card_tab_id,
                title,
                existing[2],
            )
            existing[2].refresh()
            return

        retain = getattr(self.ctx.ui, "retain_resolved_document", None)
        if callable(retain):
            retain(tab_id)
        self._card_retains.add(card_tab_id)

        card = ItemCardView(
            get_entry=lambda: self._item_entry(tab_id, section),
            get_image=self._image,
            can_edit=lambda: self._can_edit_tab(tab_id),
            on_replace_line=lambda line, text: self._replace_line(
                tab_id,
                line,
                text,
            ),
            on_delete_line=lambda line: self._delete_line(
                tab_id,
                line,
            ),
            on_insert_line=lambda line, text: self._insert_line(
                tab_id,
                line,
                text,
            ),
            on_field_edit=lambda section, key, value: self._apply_field(
                tab_id,
                section,
                key,
                value,
            ),
            on_field_rename=lambda section, old_key, new_key, value: (
                self._rename_field(
                    tab_id,
                    section,
                    old_key,
                    new_key,
                    value,
                )
            ),
            on_text_edit=lambda section, field, value: (
                self._apply_item_text(
                    tab_id,
                    section,
                    field,
                    value,
                )
            ),
            on_go_to_line=lambda line: self._go_to_line(tab_id, line),
            on_save=self._save_now,
            on_undo=lambda: self.ctx.ui.undo_resolved_document(tab_id),
            on_redo=lambda: self.ctx.ui.redo_resolved_document(tab_id),
            on_open_document=lambda: self._go_to_section(
                tab_id,
                section,
            ),
            on_open_text_source=self._open_text_source,
            theme_color=self.ctx.ui.theme_color,
            color_defs=self._color_definitions,
            section=section,
            document_title=self._document_title(tab_id),
            preview=False,
        )
        self._cards[card_tab_id] = (tab_id, section, card)
        self.ctx.ui.open_editor_tab(card_tab_id, title, card)
        card.refresh()

    def _can_edit_tab(self, tab_id):
        has = getattr(self.ctx.ui, "has_resolved_document", None)
        if callable(has) and has(tab_id):
            return bool(self.ctx.ui.is_project_active())
        return bool(
            self.ctx.ui.is_project_active()
            and self.ctx.ui.resolved_draft(tab_id) is not None
        )

    # -------------------------------------------------- правка
    @staticmethod
    def _same_path(left, right):
        return sdk_same_path(left, right)

    def _snapshot(self, tab_id):
        reader = getattr(self.ctx.ui, "resolved_draft", None)
        if not callable(reader):
            return None
        try:
            return reader(tab_id)
        except Exception:
            return None

    def _new_file_for_line(self, tab_id, line_number):
        overlay = self._project_overlay()
        if overlay is None:
            return None

        snapshot = self._snapshot(tab_id)
        if snapshot is None:
            return None
        text = snapshot.text
        sources = tuple(snapshot.line_sources or ())

        new_files = {
            Path(path) for path in (overlay.new_files or ())
        }
        if not new_files:
            return None

        index = int(line_number) - 1
        if 0 <= index < len(sources):
            source = sources[index]
            path = getattr(source, "path", None)
            if path is not None:
                for candidate in new_files:
                    if self._same_path(candidate, path):
                        return candidate

        section = self.ctx.services.ltx_section_at_line(text, line_number)
        if not section:
            return None

        for path, text in (overlay.source_files or {}).items():
            candidate = Path(path)
            if candidate not in new_files:
                continue
            if (
                self.ctx.services.ltx_section_bounds(
                    str(text or ""),
                    section,
                )
                is not None
            ):
                return candidate

        return None

    def _edit_new_item_file(self, tab_id, line_number, action, new_line=None):
        path = self._new_file_for_line(tab_id, line_number)
        if path is None:
            return False

        snapshot = self._snapshot(tab_id)
        if snapshot is None:
            return False
        text = snapshot.text

        section = self.ctx.services.ltx_section_at_line(text, line_number)
        if not section:
            return False

        lines = text.split("\n")
        index = int(line_number) - 1
        old_line = lines[index] if 0 <= index < len(lines) else ""
        key = self.ctx.services.ltx_key_from_line(old_line)

        text = self.ctx.ui.read_project_text(path)
        updated = self.ctx.services.ltx_edit_section(
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
        if str(tab_id) == self._tab_id:
            self.show()
        return True

    def _can_edit(self):
        return bool(
            self.ctx.ui.is_project_active()
            and self._root_path is not None
            and self._tab_id
            and self._snapshot(self._tab_id) is not None
        )

    def _apply_field(self, tab_id, section, key, value):
        fn = getattr(self.ctx.ui, "apply_resolved_field_edit", None)
        if not callable(fn):
            return False
        ok = bool(fn(tab_id, section, key, value))
        if not ok:
            self.ctx.ui.show_error(
                "Не удалось изменить поле %s в секции [%s]."
                % (key, section)
            )
        return ok

    def _rename_field(self, tab_id, section, old_key, new_key, value):
        fn = getattr(self.ctx.ui, "rename_resolved_field", None)
        if not callable(fn):
            return False
        ok = bool(fn(tab_id, section, old_key, new_key, value))
        if not ok:
            self.ctx.ui.show_error(
                "Не удалось переименовать поле %s в секции [%s]."
                % (old_key, section)
            )
        return ok

    def _apply_item_text(self, tab_id, section, field, value):
        """Изменить название/описание через XML строковой таблицы.

        Правка идёт в тот XML-документ, который дал текущий перевод, и
        попадает в проектный черновик. Дальше с ним работает обычный
        XML-конвейер проекта (DXML-Lua / XML / Override), поэтому
        отображаемый текст и сохраняемый патч остаются согласованными.
        """
        if not self._can_edit_tab(tab_id):
            self.ctx.ui.show_error(
                "Создайте проект, чтобы изменять название и описание."
            )
            return False

        entry = self._item_entry(tab_id, section)
        if entry is None:
            self.ctx.ui.show_error(
                "Секция [%s] не является предметом." % section
            )
            return False

        if str(field).lower() == "name":
            source = getattr(entry, "name_source", None)
            label = "названия"
        else:
            source = getattr(entry, "description_source", None)
            label = "описания"

        if source is None or getattr(source, "path", None) is None:
            self.ctx.ui.show_error(
                "Не найден ключ локализации для %s секции [%s]."
                % (label, section)
            )
            return False

        path = Path(source.path)
        key = str(getattr(source, "key", "") or "")
        if not key:
            self.ctx.ui.show_error(
                "У строки локализации отсутствует id."
            )
            return False

        reader = getattr(self.ctx.ui, "read_project_text", None)
        writer = getattr(self.ctx.ui, "write_project_text", None)
        async_writer = getattr(
            self.ctx.ui,
            "write_project_text_async",
            None,
        )
        editor = getattr(self.ctx.services, "edit_localization_text", None)
        if not callable(reader) or not callable(editor):
            self.ctx.ui.show_error(
                "Платформа не предоставляет доступ к черновикам XML."
            )
            return False

        current = reader(path)
        updated = editor(
            current,
            key,
            str(value or ""),
        )
        if updated is None:
            self.ctx.ui.show_error(
                "Не удалось найти строку %s в %s."
                % (key, Path(path).name)
            )
            return False

        if updated == current:
            return True

        if callable(async_writer):
            return self._start_item_text_save(
                tab_id,
                section,
                path,
                key,
                label,
                current,
                updated,
            )

        if not callable(writer):
            self.ctx.ui.show_error(
                "Платформа не предоставляет запись черновиков XML."
            )
            return False

        if not writer(path, updated):
            return False

        self.ctx.ui.log(
            "Проект: изменён текст %s (%s) в %s"
            % (key, label, Path(path).name)
        )
        self.ctx.ui.set_status(
            "Текст предмета записан в черновик XML: %s" % Path(path).name,
            5000,
        )
        self._refresh_item_text(tab_id, section)
        return True

    def _text_save_cards(self, tab_id, section):
        """Карточки, которые показывают сохраняемый предмет."""
        wanted = str(section or "").lower()
        cards = []

        for card_tab_id, record in list(self._cards.items()):
            root_tab, card_section, card = record
            if (
                root_tab == tab_id
                and card_section.lower() == wanted
            ):
                cards.append((card_tab_id, card))

        if (
            self._window is not None
            and self._window.card.section().lower() == wanted
        ):
            cards.append((None, self._window.card))

        return cards

    def _set_save_busy(self, job, busy):
        """Показать/снять «часы» и блокировку ввода на карточках."""
        for card_tab_id, card in job.get("cards", ()):
            try:
                card.set_saving(busy)
            except Exception:
                pass

            if card_tab_id:
                setter = getattr(
                    self.ctx.ui,
                    "set_editor_tab_busy",
                    None,
                )
                if callable(setter):
                    try:
                        setter(card_tab_id, bool(busy))
                    except Exception:
                        pass

    def _start_item_text_save(
        self,
        tab_id,
        section,
        path,
        key,
        label,
        current,
        updated,
    ):
        """Поставить запись XML в очередь и заблокировать представления."""
        path = Path(path)
        path_key = sdk_path_key(path)

        if path_key in self._text_save_jobs:
            self.ctx.ui.set_status(
                "Файл уже сохраняется: %s" % path.name,
                5000,
            )
            return False

        writer = getattr(
            self.ctx.ui,
            "write_project_text_async",
            None,
        )
        if not callable(writer):
            return False

        self._text_save_seq += 1
        job = {
            "token": self._text_save_seq,
            "tab_id": str(tab_id or ""),
            "section": str(section or ""),
            "path": path,
            "key": key,
            "label": label,
            "text": updated,
            "cards": self._text_save_cards(tab_id, section),
        }
        self._text_save_jobs[path_key] = job
        self._set_save_busy(job, True)
        self.ctx.ui.set_status(
            "Сохранение текста: %s…" % path.name,
        )

        def done(ok, error):
            self._on_item_text_saved(job, bool(ok), error)

        try:
            started = _call_async_text_save(
                writer,
                path,
                updated,
                done,
                current,
            )
        except Exception as exc:
            started = False
            self.ctx.ui.log(
                "Предметы: не удалось запустить сохранение %s: %s"
                % (path.name, exc)
            )

        if not started:
            if self._text_save_jobs.get(path_key) is job:
                self._text_save_jobs.pop(path_key, None)
            self._set_save_busy(job, False)
            self.ctx.ui.show_error(
                "Не удалось запустить сохранение XML %s." % path.name
            )
            return False
        return True

    def _on_item_text_saved(self, job, ok, error):
        if self._disposed:
            return

        path = Path(job["path"])
        if not ok:
            if error:
                self.ctx.ui.show_error(
                    "Не удалось сохранить XML %s:\n%s"
                    % (path.name, error)
                )
            self.ctx.ui.set_status(
                "Ошибка сохранения текста: %s" % path.name,
                5000,
            )
            self._apply_item_text_entry(
                job,
                self._item_entry(job["tab_id"], job["section"]),
            )
            self._finish_item_text_save(job)
            return

        self.ctx.ui.log(
            "Проект: изменён текст %s (%s) в %s"
            % (job["key"], job["label"], path.name)
        )
        self.ctx.ui.set_status(
            "Текст предмета сохранён в XML: %s" % path.name,
            5000,
        )
        self._refresh_item_text_async(job)

    def _refresh_item_text_async(self, job):
        """Пересобрать карточку без блокировки UI-потока."""
        tab_id = job["tab_id"]
        section = job["section"]

        forest = None
        if self.session is not None:
            forest = getattr(self.session, "forest", None)

        draft = None
        try:
            draft = self.ctx.ui.resolved_draft(tab_id)
        except Exception:
            draft = None

        run = getattr(getattr(self.ctx, "tasks", None), "run", None)
        overlay = self._project_overlay()

        if (
            forest is not None
            and draft is not None
            and callable(run)
        ):
            text = draft.text
            builder = getattr(
                self.ctx.services,
                "build_item_entry",
                None,
            )

            def work(_log):
                return _call_with_overlay(
                    builder,
                    forest,
                    text,
                    section,
                    overlay=overlay,
                )

            try:
                run(
                    work,
                    on_done=lambda entry, error: (
                        self._on_item_text_refreshed(job, entry, error)
                    ),
                )
                return
            except Exception as exc:
                self.ctx.ui.log(
                    "Предметы: фоновая пересборка карточки недоступна: %s"
                    % exc
                )

        self._apply_item_text_entry(
            job,
            self._item_entry(tab_id, section),
        )
        self._finish_item_text_save(job)

    def _on_item_text_refreshed(self, job, entry, error):
        if self._disposed:
            return

        if error is not None:
            self.ctx.ui.log(
                "Предметы: не удалось обновить карточку после сохранения: %s"
                % error
            )
            entry = self._item_entry(job["tab_id"], job["section"])

        self._apply_item_text_entry(job, entry)
        self._finish_item_text_save(job)

    def _apply_item_text_entry(self, job, entry):
        section = job["section"]
        if self._window is not None:
            try:
                self._window.update_section(section, entry)
            except Exception:
                pass

        for _card_tab_id, card in job.get("cards", ()):
            try:
                if card.section().lower() == str(section).lower():
                    card.refresh(entry)
            except Exception:
                pass

    def _finish_item_text_save(self, job):
        path_key = sdk_path_key(job["path"])
        current = self._text_save_jobs.get(path_key)
        if current is not None and current.get("token") == job.get("token"):
            self._text_save_jobs.pop(path_key, None)
            self._set_save_busy(job, False)

    def _refresh_item_text(self, tab_id, section):
        entry = self._item_entry(tab_id, section)
        if self._window is not None:
            self._window.update_section(section, entry)
        for record in list(self._cards.values()):
            root_tab, card_section, card = record
            if (
                root_tab == tab_id
                and card_section.lower() == str(section).lower()
            ):
                card.refresh()

    def _replace_line(self, tab_id, line_number, new_line):
        if self._new_file_for_line(tab_id, line_number) is not None:
            return self._edit_new_item_file(
                tab_id,
                line_number,
                "replace",
                new_line=new_line,
            )

        ok = self.ctx.ui.apply_resolved_line_edit(
            tab_id,
            "replace",
            line_number,
            str(new_line).rstrip("\r\n"),
        )
        if not ok:
            self.ctx.ui.show_error(
                "Не удалось изменить строку %s в resolved-документе."
                % line_number
            )
        return ok

    def _delete_line(self, tab_id, line_number):
        if self._new_file_for_line(tab_id, line_number) is not None:
            return self._edit_new_item_file(
                tab_id,
                line_number,
                "delete",
            )

        ok = self.ctx.ui.apply_resolved_line_edit(
            tab_id,
            "delete",
            line_number,
        )
        if not ok:
            self.ctx.ui.show_error(
                "Не удалось удалить строку %s в resolved-документе."
                % line_number
            )
        return ok

    def _insert_line(self, tab_id, line_number, new_line):
        if self._new_file_for_line(tab_id, line_number) is not None:
            return self._edit_new_item_file(
                tab_id,
                line_number,
                "insert",
                new_line=new_line,
            )

        ok = self.ctx.ui.apply_resolved_line_edit(
            tab_id,
            "insert",
            line_number,
            str(new_line).rstrip("\r\n"),
        )
        if not ok:
            self.ctx.ui.show_error(
                "Не удалось вставить строку после %s в resolved-документе."
                % line_number
            )
        return ok

    def _open_text_source(self, source):
        """Открыть XML-файл на строке объявления строки локализации."""
        path = getattr(source, "path", None)
        if path is None:
            return False

        line = int(getattr(source, "line", 0) or 0)
        opener = getattr(self.ctx.ui, "open_raw_at_line", None)
        if callable(opener) and line > 0:
            opener(path, line)
            return True

        opener = getattr(self.ctx.ui, "open_raw", None)
        if callable(opener):
            opener(path)
            return True
        return False

    def _go_to_line(self, tab_id, line_number):
        opener = getattr(self.ctx.ui, "open_resolved_document", None)
        if callable(opener):
            opener(tab_id, int(line_number))
            return

        snapshot = self._snapshot(tab_id)
        if snapshot is None:
            self.ctx.ui.show_error(
                "Не удалось определить документ для перехода."
            )
            return
        self.ctx.ui.open_resolved_tab(
            tab_id,
            self._title if tab_id == self._tab_id else tab_id,
            snapshot.text,
            snapshot.line_sources,
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

        known_sections = set(
            self.ctx.services.ltx_declared_sections(item_text)
        )
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

        if not self.ctx.ui.write_project_texts(
            (
                (item_path, item_text),
                (xml_path, xml_text),
            )
        ):
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
        """Обновить VFS после создания файлов проекта.

        Само добавление файлов в лес — ответственность WorkspaceService
        платформы: модуль не мутирует LtxForest напрямую.
        """
        if self.session is None or not getattr(
            self.session,
            "has_tree",
            False,
        ):
            return
        refresh = getattr(self.ctx.ui, "refresh_workspace", None)
        if callable(refresh):
            refresh()
        self.show()


class ClickToEditLineEdit(QLineEdit):
    """Название выглядит как подпись, пока пользователь не кликнул."""

    _IDLE_STYLE = (
        "QLineEdit { background: transparent; border: none; "
        "padding: 0; font-weight: 600; }"
    )
    _DEFAULT_BORDER = "#3C3F41"
    _DEFAULT_ACCENT = "#3592E4"
    _EDIT_STYLE_TEMPLATE = (
        "QLineEdit { background: transparent; "
        "border: 1px solid %(border)s; padding: 2px; "
        "font-weight: 600; }"
        "QLineEdit:focus { border-color: %(accent)s; }"
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._edit_allowed = False
        self._editing = False
        self._edit_style = self._EDIT_STYLE_TEMPLATE % {
            "border": self._DEFAULT_BORDER,
            "accent": self._DEFAULT_ACCENT,
        }
        self.setFrame(False)
        self.setReadOnly(True)
        self.setStyleSheet(self._IDLE_STYLE)

    def set_theme_colors(self, border=None, accent=None):
        """Цвета рамки поля в режиме правки под текущую тему."""
        self._edit_style = self._EDIT_STYLE_TEMPLATE % {
            "border": border or self._DEFAULT_BORDER,
            "accent": accent or self._DEFAULT_ACCENT,
        }
        if self._editing:
            self.setStyleSheet(self._edit_style)

    def set_edit_allowed(self, allowed):
        """Разрешение на правку: ``bool`` или предикат.

        Предикат проверяется в момент клика, а не в момент отрисовки:
        проект может появиться уже после открытия карточки, и право на
        правку не должно «застывать» до следующего refresh.
        """
        self._edit_allowed = allowed if callable(allowed) else bool(allowed)
        if not callable(allowed):
            self.end_editing()
        self._update_cursor()

    def _is_edit_allowed(self):
        allowed = self._edit_allowed
        if callable(allowed):
            try:
                return bool(allowed())
            except Exception:
                return False
        return bool(allowed)

    def _update_cursor(self):
        self.setCursor(
            Qt.IBeamCursor if self._is_edit_allowed() else Qt.ArrowCursor
        )

    def enterEvent(self, event):
        self._update_cursor()
        super().enterEvent(event)

    def is_editing(self):
        return self._editing

    def begin_editing(self):
        if not self._is_edit_allowed():
            return False
        self._editing = True
        self.setReadOnly(False)
        self.setStyleSheet(self._edit_style)
        self.setFocus(Qt.MouseFocusReason)
        return True

    def end_editing(self):
        self._editing = False
        self.setReadOnly(True)
        self.setStyleSheet(self._IDLE_STYLE)

    def mousePressEvent(self, event):
        if (
            not self._editing
            and event.button() == Qt.LeftButton
            and self._is_edit_allowed()
        ):
            self.begin_editing()
        super().mousePressEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        if self._editing:
            self.end_editing()


class LocalizedDescriptionEdit(QPlainTextEdit):
    """Описание выглядит как подпись, пока пользователь не кликнул.

    ``QPlainTextEdit`` не имеет ``editingFinished``; карточке нужно
    зафиксировать правку при потере фокуса или по Ctrl+Enter, не записывая
    XML на каждый символ.
    """

    editing_finished = pyqtSignal()
    editing_changed = pyqtSignal(bool)

    _IDLE_STYLE = (
        "QPlainTextEdit { background: transparent; border: none; }"
    )
    _DEFAULT_BORDER = "#3C3F41"
    _DEFAULT_ACCENT = "#3592E4"
    _EDIT_STYLE_TEMPLATE = (
        "QPlainTextEdit { background: transparent; "
        "border: 1px solid %(border)s; }"
        "QPlainTextEdit:focus { border-color: %(accent)s; }"
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._edit_allowed = False
        self._editing = False
        self._edit_style = self._EDIT_STYLE_TEMPLATE % {
            "border": self._DEFAULT_BORDER,
            "accent": self._DEFAULT_ACCENT,
        }
        # Исходная строка с ``\n`` и ``%c[...]``. В обычном состоянии
        # виджет рисует её как движок, в режиме правки — как XML.
        self._raw_text = ""
        self._engine_colors = {}
        self._engine_default = None
        self._color_names = ["default"]

        self._completer = QCompleter(self)
        self._completer.setWidget(self)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchContains)
        self._completer.activated[QModelIndex].connect(
            self._on_color_activated
        )
        self._color_model = QStandardItemModel(self)
        self._completer.setModel(self._color_model)
        self.set_color_suggestions({})

        self.textChanged.connect(self._update_color_completion)
        self.cursorPositionChanged.connect(self._update_color_completion)

        self.setFrameShape(QFrame.NoFrame)
        self.setReadOnly(True)
        self.setStyleSheet(self._IDLE_STYLE)

    def set_engine_text(self, raw_text, colors=None, default_color=None):
        """Показать текст с оформлением движка.

        ``raw_text`` остаётся доступен редактору без изменений; цвета
        применяются только к отрисованному виду.
        """
        self._raw_text = engine_source_text(raw_text)
        self._engine_default = default_color
        self.set_color_suggestions(colors)

        if self._editing:
            QPlainTextEdit.setPlainText(
                self,
                self._raw_to_editor_text(self._raw_text),
            )
        else:
            self._render_engine_text()

    def set_color_suggestions(self, colors=None):
        """Обновить список цветов для подсказки после ``%c[``."""
        palette = dict(colors or {})
        names = sorted(
            (
                str(name)
                for name in palette
                if str(name).lower() != "default"
            ),
            key=str.lower,
        )
        self._color_names = ["default"] + names
        self._engine_colors = palette

        model = self._color_model
        model.clear()
        default_color = self._default_engine_color()
        for name in self._color_names:
            item = QStandardItem(name)
            color = None
            if name.lower() == "default":
                color = default_color
            else:
                value = palette.get(name)
                if value is None:
                    for key, candidate in palette.items():
                        if str(key).lower() == name.lower():
                            value = candidate
                            break
                if value is not None:
                    color = QColor(
                        int(value[0]) & 0xFF,
                        int(value[1]) & 0xFF,
                        int(value[2]) & 0xFF,
                    )
            if color is not None and color.isValid():
                swatch = QPixmap(12, 12)
                swatch.fill(color)
                item.setIcon(QIcon(swatch))
            model.appendRow(item)

        self._completer.popup().hide()

    def _color_prefix_context(self):
        """Контекст ``%c[...`` под курсором: (начало содержимого, prefix)."""
        if not self._editing:
            return None

        text = self.toPlainText()
        position = self.textCursor().position()
        start = text.lower().rfind("%c[", 0, position)
        if start < 0:
            return None

        content_start = start + 3
        if "]" in text[content_start:position]:
            return None

        prefix = text[content_start:position]
        if "\n" in prefix or "\r" in prefix:
            return None
        return content_start, prefix

    def _update_color_completion(self):
        if not self._editing:
            self._completer.popup().hide()
            return

        context = self._color_prefix_context()
        if context is None:
            self._completer.popup().hide()
            return

        _content_start, prefix = context
        self._completer.setCompletionPrefix(prefix)
        model = self._completer.completionModel()
        if model.rowCount() == 0:
            self._completer.popup().hide()
            return

        popup = self._completer.popup()
        popup.setCurrentIndex(model.index(0, 0))
        rect = self.cursorRect()
        rect.moveTopLeft(self.viewport().mapTo(self, rect.topLeft()))
        rect.setWidth(
            max(180, popup.sizeHintForColumn(0) + 28)
        )
        self._completer.complete(rect)

    def _on_color_activated(self, index):
        if index is not None and index.isValid():
            self._insert_color_completion(index.data(Qt.DisplayRole))

    def _insert_color_completion(self, completion):
        completion = str(completion or "").strip()
        if not completion:
            return

        context = self._color_prefix_context()
        if context is None:
            return

        content_start, _prefix = context
        text = self.toPlainText()
        cursor = self.textCursor()
        position = cursor.position()

        end = text.find("]", position)
        if end >= 0 and "\n" not in text[position:end]:
            replace_end = end + 1
        else:
            replace_end = position

        cursor.setPosition(content_start)
        cursor.setPosition(replace_end, QTextCursor.KeepAnchor)
        cursor.insertText(completion + "]")
        self.setTextCursor(cursor)
        self._completer.popup().hide()

    def raw_text(self):
        """Текст в том виде, в котором его нужно записать в XML."""
        if self._editing:
            return self._editor_text_to_raw(self.toPlainText())
        return self._raw_text

    @staticmethod
    def _raw_to_editor_text(text):
        """XML-строка → текст редактора: литералы ``\\n`` → переносы."""
        return str(text or "").replace("\\n", "\n")

    @staticmethod
    def _editor_text_to_raw(text):
        """Текст редактора → XML-строка: переносы → литералы ``\\n``."""
        normalized = (
            str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        )
        return normalized.replace("\n", "\\n")

    def setPlainText(self, text):
        self._raw_text = str(text or "")
        super().setPlainText(self._raw_text)

    def clear(self):
        self._raw_text = ""
        super().clear()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._editing:
            self._paint_newline_markers()

    def _paint_newline_markers(self):
        """Показать призрачные ``\\n`` в конце каждой строки редактора.

        В редакторе литералы ``\\n`` превращены в настоящие переносы.
        Маркер не является частью текста: он только напоминает, что в
        XML на этом месте записан ``\\n``.
        """
        viewport = self.viewport()
        if viewport is None:
            return

        painter = QPainter(viewport)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            font = QFont(self.font())
            if font.pointSizeF() > 0:
                font.setPointSizeF(max(6.0, font.pointSizeF() - 2.0))
            else:
                font.setPixelSize(max(7, font.pixelSize() - 2))
            painter.setFont(font)
            metrics = QFontMetrics(font)

            label = "\\n"
            width = metrics.width(label) + 10
            height = metrics.height() + 2

            text_color = self.palette().color(QPalette.Text)
            if not text_color.isValid():
                text_color = QColor("#787878")
            text_color.setAlpha(160)
            fill = QColor(text_color)
            fill.setAlpha(24)
            border = QColor(text_color)
            border.setAlpha(70)

            block = self.document().firstBlock()
            while block.isValid() and block.next().isValid():
                cursor = QTextCursor(block)
                cursor.movePosition(QTextCursor.EndOfBlock)
                rect = self.cursorRect(cursor)
                x = rect.right() + 6
                if x + width > viewport.width() - 2:
                    x = max(2, viewport.width() - width - 2)
                y = rect.top() + (rect.height() - height) // 2
                box = QRectF(x, y, width, height)

                painter.setPen(QPen(border, 1))
                painter.setBrush(fill)
                painter.drawRoundedRect(box, 3, 3)
                painter.setPen(text_color)
                painter.drawText(box, Qt.AlignCenter, label)
                block = block.next()
        finally:
            painter.end()

    def _default_engine_color(self):
        value = self._engine_default
        if isinstance(value, QColor):
            return value
        if isinstance(value, (tuple, list)) and len(value) >= 3:
            return QColor(
                int(value[0]) & 0xFF,
                int(value[1]) & 0xFF,
                int(value[2]) & 0xFF,
            )
        color = QColor(str(value or ""))
        if color.isValid():
            return color
        return self.palette().color(QPalette.Text)

    def _render_engine_text(self):
        default = self._default_engine_color()
        document = self.document()
        document.clear()
        cursor = QTextCursor(document)

        runs = parse_engine_text(
            self._raw_text,
            self._engine_colors,
            (default.red(), default.green(), default.blue()),
        )
        for run in runs:
            if run.color is None:
                color = default
            else:
                color = QColor(
                    int(run.color[0]) & 0xFF,
                    int(run.color[1]) & 0xFF,
                    int(run.color[2]) & 0xFF,
                )
            char_format = QTextCharFormat()
            char_format.setForeground(color)

            # Движок не считает реальные CR/LF/TAB переводами строк:
            # в шрифтовом атласе у управляющих символов нулевая ширина.
            # Перенос делает только литерал ``\n`` (``CUILine``).
            text = "".join(
                ch for ch in run.text if ord(ch) >= 0x20
            ).replace("\\n", "\n")
            for index, part in enumerate(text.split("\n")):
                if index:
                    cursor.insertBlock()
                if part:
                    cursor.insertText(part, char_format)

    def set_theme_colors(self, border=None, accent=None):
        """Цвета рамки поля в режиме правки под текущую тему."""
        self._edit_style = self._EDIT_STYLE_TEMPLATE % {
            "border": border or self._DEFAULT_BORDER,
            "accent": accent or self._DEFAULT_ACCENT,
        }
        if self._editing:
            self.setStyleSheet(self._edit_style)

    def set_edit_allowed(self, allowed):
        """Разрешение на правку: ``bool`` или предикат.

        Предикат проверяется в момент клика, а не в момент отрисовки:
        проект может появиться уже после открытия карточки, и право на
        правку не должно «застывать» до следующего refresh.
        """
        self._edit_allowed = allowed if callable(allowed) else bool(allowed)
        if not callable(allowed):
            self.end_editing()
        self._update_cursor()

    def _is_edit_allowed(self):
        allowed = self._edit_allowed
        if callable(allowed):
            try:
                return bool(allowed())
            except Exception:
                return False
        return bool(allowed)

    def _update_cursor(self):
        self.setCursor(
            Qt.IBeamCursor if self._is_edit_allowed() else Qt.ArrowCursor
        )

    def enterEvent(self, event):
        self._update_cursor()
        super().enterEvent(event)

    def is_editing(self):
        return self._editing

    def begin_editing(self):
        if not self._is_edit_allowed():
            return False
        self._editing = True
        # В редакторе литералы ``\n`` превращаются в настоящие переносы,
        # а их места помечаются призрачными значками ``\n``. Метки
        # ``%c[...]`` остаются видимым текстом.
        QPlainTextEdit.setPlainText(
            self,
            self._raw_to_editor_text(self._raw_text),
        )
        self.setReadOnly(False)
        self.setStyleSheet(self._edit_style)
        self.setFocus(Qt.MouseFocusReason)
        self.viewport().update()
        self.editing_changed.emit(True)
        return True

    def end_editing(self):
        was_editing = self._editing
        if was_editing:
            self._raw_text = self._editor_text_to_raw(self.toPlainText())
        self._editing = False
        self._completer.popup().hide()
        self.setReadOnly(True)
        self.setStyleSheet(self._IDLE_STYLE)
        if was_editing:
            self._render_engine_text()
            self.editing_changed.emit(False)
        self.viewport().update()

    def mousePressEvent(self, event):
        if (
            not self._editing
            and event.button() == Qt.LeftButton
            and self._is_edit_allowed()
        ):
            self.begin_editing()
        super().mousePressEvent(event)

    def focusOutEvent(self, event):
        was_editing = self._editing
        super().focusOutEvent(event)
        if was_editing:
            self.end_editing()
            self.editing_finished.emit()

    def keyPressEvent(self, event):
        if (
            event.key() in (Qt.Key_Return, Qt.Key_Enter)
            and event.modifiers() & Qt.ControlModifier
        ):
            self._completer.popup().hide()
            self.editing_finished.emit()
            event.accept()
            return

        popup = self._completer.popup()
        if popup.isVisible():
            if event.key() == Qt.Key_Escape:
                popup.hide()
                event.accept()
                return
            if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Tab):
                index = popup.currentIndex()
                if index.isValid():
                    self._insert_color_completion(
                        index.data(Qt.DisplayRole)
                    )
                    event.accept()
                    return
            if event.key() in (Qt.Key_Up, Qt.Key_Down):
                model = popup.model()
                row = popup.currentIndex().row()
                if event.key() == Qt.Key_Up:
                    row = max(0, row - 1)
                else:
                    row = min(model.rowCount() - 1, row + 1)
                if row >= 0:
                    popup.setCurrentIndex(model.index(row, 0))
                event.accept()
                return
            if event.key() in (Qt.Key_PageUp, Qt.Key_PageDown):
                model = popup.model()
                row = popup.currentIndex().row()
                step = max(1, popup.viewport().height() // 20)
                if event.key() == Qt.Key_PageUp:
                    row = max(0, row - step)
                else:
                    row = min(model.rowCount() - 1, row + step)
                if row >= 0:
                    popup.setCurrentIndex(model.index(row, 0))
                event.accept()
                return

        super().keyPressEvent(event)


class ItemFieldsTable(QTableWidget):
    """Таблица полей с проверкой права на правку в момент вызова.

    ``QTableWidget`` решает, открывать ли редактор, по флагам ячеек,
    которые выставляются при отрисовке. Проект же может появиться позже
    (окно/вкладка уже открыты), поэтому право проверяется динамически,
    а не кешируется в флагах.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._edit_allowed = True

    def set_edit_allowed(self, allowed):
        self._edit_allowed = allowed if callable(allowed) else bool(allowed)

    def _is_edit_allowed(self):
        allowed = self._edit_allowed
        if callable(allowed):
            try:
                return bool(allowed())
            except Exception:
                return False
        return bool(allowed)

    def edit(self, index, *args):
        if not self._is_edit_allowed():
            return False
        return super().edit(index, *args)


class ItemCardView(QWidget):
    """Карточка предмета.

    Один и тот же виджет используется как preview в окне-пикере и как
    самостоятельная вкладка. Значения полей не хранятся здесь: виджет
    каждый раз перечитывает проекцию из живого документа.
    """

    def __init__(
        self,
        get_entry,
        get_image,
        can_edit,
        on_replace_line=None,
        on_delete_line=None,
        on_insert_line=None,
        on_field_edit=None,
        on_field_rename=None,
        on_text_edit=None,
        on_go_to_line=None,
        on_save=None,
        on_undo=None,
        on_redo=None,
        on_open_as_tab=None,
        on_open_document=None,
        on_open_text_source=None,
        theme_color=None,
        color_defs=None,
        section=None,
        document_title="",
        preview=False,
        parent=None,
    ):
        super().__init__(parent)

        self._get_entry = get_entry
        self._get_image = get_image
        self._can_edit = can_edit or (lambda: False)
        self._on_replace_line = on_replace_line
        self._on_delete_line = on_delete_line
        self._on_insert_line = on_insert_line
        self._on_field_edit = on_field_edit
        self._on_field_rename = on_field_rename
        self._on_text_edit = on_text_edit
        self._on_go_to_line = on_go_to_line
        self._on_save = on_save
        self._on_undo = on_undo
        self._on_redo = on_redo
        self._on_open_as_tab = on_open_as_tab
        self._on_open_document = on_open_document
        self._on_open_text_source = on_open_text_source
        self._theme_color = theme_color or (
            lambda name, fallback=None: fallback
        )
        # Цвета ``%c[name]`` из ``ui/color_defs.xml``: словарь или
        # функция, чтобы карточка видела проект, созданный позже.
        self._color_defs = color_defs if color_defs is not None else {}
        self._section = str(section or "")
        self._document_title = str(document_title or "")
        self._preview = bool(preview)
        self._entry = None
        self._populating_fields = False
        self._populating_text = False
        self._saving = False
        self._shown_name = ""
        self._shown_description = ""

        self._placeholder = _placeholder_pixmap(
            self._palette("text_muted", "#787878")
        )
        self._build_ui()
        self._init_shortcuts()

    # -------------------------------------------------- UI
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        top = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setContentsMargins(0, 0, 0, 0)
        title_box.setSpacing(0)

        self.section_label = QLabel("—")
        self.section_label.setStyleSheet("font-weight: 600;")
        self.section_label.setWordWrap(True)
        title_box.addWidget(self.section_label)

        self.source_label = QLabel()
        self.source_label.setProperty("role", "muted")
        self.source_label.setWordWrap(True)
        self.source_label.setVisible(bool(self._document_title))
        if self._document_title:
            self.source_label.setText(
                "Документ: %s" % self._document_title
            )
        title_box.addWidget(self.source_label)
        top.addLayout(title_box, 1)

        self.open_tab_btn = QToolButton()
        self.open_tab_btn.setText("⧉")
        self.open_tab_btn.setToolTip("Открыть карточку как вкладку")
        self.open_tab_btn.setAutoRaise(True)
        self.open_tab_btn.clicked.connect(self._open_as_tab)
        self.open_tab_btn.setVisible(
            self._preview and self._on_open_as_tab is not None
        )
        top.addWidget(self.open_tab_btn)

        self.open_doc_btn = QToolButton()
        self.open_doc_btn.setText("Перейти к объявлению")
        self.open_doc_btn.setToolTip(
            "Перейти к объявлению секции в документе"
        )
        self.open_doc_btn.setAutoRaise(True)
        self.open_doc_btn.clicked.connect(self._open_document)
        self.open_doc_btn.setVisible(
            not self._preview and self._on_open_document is not None
        )
        top.addWidget(self.open_doc_btn)

        self.save_btn = QToolButton()
        self.save_btn.setText("Сохранить")
        self.save_btn.setToolTip("Сохранить черновик (Ctrl+S)")
        self.save_btn.setAutoRaise(True)
        self.save_btn.clicked.connect(self._save)
        self.save_btn.setVisible(not self._preview and self._on_save is not None)
        top.addWidget(self.save_btn)

        layout.addLayout(top)

        header = QHBoxLayout()

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(190, 190)
        self.icon_label.setAlignment(Qt.AlignCenter)
        header.addWidget(self.icon_label, 0, Qt.AlignTop)

        text_block = QVBoxLayout()
        self.name_label = ClickToEditLineEdit()
        self.name_label.setPlaceholderText("Название предмета")
        self.name_label.editingFinished.connect(self._apply_name_text)
        self.name_label.set_theme_colors(
            self._palette("border", "#3C3F41"),
            self._palette("accent", "#3592E4"),
        )
        self.name_label.setContextMenuPolicy(Qt.CustomContextMenu)
        self.name_label.customContextMenuRequested.connect(
            self._on_name_context_menu
        )
        text_block.addWidget(self.name_label)

        self.meta_label = QLabel()
        self.meta_label.setWordWrap(True)
        self.meta_label.setProperty("role", "muted")
        text_block.addWidget(self.meta_label)

        text_block.addStretch(1)
        header.addLayout(text_block, 1)
        layout.addLayout(header)

        self.description_panel = QWidget()
        description_layout = QVBoxLayout(self.description_panel)
        description_layout.setContentsMargins(0, 0, 0, 0)
        description_layout.setSpacing(2)

        self.description_caption = QLabel("Описание")
        self.description_caption.setProperty("role", "muted")
        description_layout.addWidget(self.description_caption)

        self.description_label = LocalizedDescriptionEdit()
        self.description_label.setPlaceholderText("Описание предмета")
        self.description_label.setToolTip(
            "Клик — редактировать. Потеря фокуса или Ctrl+Enter — применить."
        )
        self.description_label.editing_finished.connect(
            self._apply_description_text
        )
        self.description_label.set_theme_colors(
            self._palette("border", "#3C3F41"),
            self._palette("accent", "#3592E4"),
        )
        self.description_label.setContextMenuPolicy(Qt.CustomContextMenu)
        self.description_label.customContextMenuRequested.connect(
            self._on_description_context_menu
        )
        self.description_label.editing_changed.connect(
            self._on_description_editing_changed
        )
        description_layout.addWidget(self.description_label, 1)

        self.description_save_btn = QPushButton("Сохранить")
        self.description_save_btn.setAutoDefault(False)
        self.description_save_btn.setDefault(False)
        self.description_save_btn.setFocusPolicy(Qt.NoFocus)
        self.description_save_btn.setToolTip(
            "Применить изменения описания (Ctrl+Enter)"
        )
        self.description_save_btn.setVisible(False)
        self.description_save_btn.clicked.connect(
            self._apply_description_text
        )
        save_row = QHBoxLayout()
        save_row.setContentsMargins(0, 0, 0, 0)
        save_row.addStretch(1)
        save_row.addWidget(self.description_save_btn)
        description_layout.addLayout(save_row)

        self.fields_table = ItemFieldsTable(0, 2)
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
        layout.addWidget(self.details_splitter, 1)

        self.empty_label = QLabel("Секция не найдена или не является предметом")
        self.empty_label.setWordWrap(True)
        self.empty_label.setProperty("role", "muted")
        self.empty_label.setVisible(False)
        layout.addWidget(self.empty_label)

    def _init_shortcuts(self):
        if self._on_save is not None:
            QShortcut(
                QKeySequence("Ctrl+S"),
                self,
                context=Qt.WidgetWithChildrenShortcut,
                activated=self._save,
            )
        if self._on_undo is not None:
            QShortcut(
                QKeySequence("Ctrl+Z"),
                self,
                context=Qt.WidgetWithChildrenShortcut,
                activated=self._undo,
            )
        if self._on_redo is not None:
            QShortcut(
                QKeySequence("Ctrl+Y"),
                self,
                context=Qt.WidgetWithChildrenShortcut,
                activated=self._redo,
            )
            QShortcut(
                QKeySequence("Ctrl+Shift+Z"),
                self,
                context=Qt.WidgetWithChildrenShortcut,
                activated=self._redo,
            )

    # -------------------------------------------------- состояние
    def section(self):
        return self._section

    def set_section(self, section):
        self._section = str(section or "")
        self.refresh()

    def refresh(self, entry=None):
        try:
            from PyQt5 import sip

            if sip.isdeleted(self):
                return
        except Exception:
            pass

        if entry is None:
            try:
                entry = self._get_entry() if self._get_entry else None
            except Exception:
                entry = None
        if entry is None:
            self._entry = None
            self._clear_details()
            self.empty_label.setVisible(True)
            return

        self.empty_label.setVisible(False)
        self._show_details(entry)

    # -------------------------------------------------- отрисовка
    def _show_details(self, entry):
        self._entry = entry
        image = self._get_image(entry.section) if self._get_image else None
        self.icon_label.setPixmap(self._detail_pixmap(image))

        self.section_label.setText("[%s]" % entry.section)

        name_source = getattr(entry, "name_source", None)
        description_source = getattr(entry, "description_source", None)
        name_editable = self._text_edit_predicate(name_source)
        description_editable = self._text_edit_predicate(
            description_source
        )

        if name_source is not None:
            # Строка существует, даже если её текст пока пуст: не
            # показываем вместо названия сырой ключ локализации.
            name = entry.name_text
        else:
            name = entry.name_text or entry.field("inv_name") or entry.section

        if description_source is not None:
            description = entry.description_text
        else:
            description = (
                entry.description_text
                or entry.field("description")
                or ""
            )
        description_raw = engine_source_text(
            self._raw_source_text(description_source, description)
        )

        self._populating_text = True
        try:
            self.name_label.setText(name)
            self.name_label.set_edit_allowed(name_editable)
            self.name_label.setToolTip(
                self._text_source_tooltip(name, name_source)
            )

            has_description = bool(description.strip()) or (
                self._text_source_editable(description_source)
            )
            self.description_panel.setVisible(has_description)
            self.description_label.set_engine_text(
                description_raw,
                self._engine_color_defs(),
                self._palette("text", "#BCBFBC"),
            )
            self.description_label.set_edit_allowed(
                description_editable
            )
            self.description_label.setToolTip(
                self._text_source_tooltip(
                    "Описание",
                    description_source,
                )
            )
            self._shown_name = name
            self._shown_description = description_raw
        finally:
            self._populating_text = False

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

        any_editable = False

        for row, field in enumerate(entry.fields):
            key_item = QTableWidgetItem(field.key)
            value_item = QTableWidgetItem(field.value)
            value_item.setToolTip(field.value)

            # Право на правку проверяет сама таблица в момент вызова:
            # проект мог появиться уже после отрисовки карточки.
            can_edit_field = field.line_number > 0
            any_editable = any_editable or can_edit_field
            for column, table_item in ((0, key_item), (1, value_item)):
                flags = table_item.flags()
                if can_edit_field:
                    flags |= Qt.ItemIsEditable
                else:
                    flags &= ~Qt.ItemIsEditable
                table_item.setFlags(flags)
                self.fields_table.setItem(row, column, table_item)

        self.fields_table.set_edit_allowed(self._can_edit)
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

    @staticmethod
    def _raw_source_text(source, fallback=""):
        """Строка для редактора: исходный ``<text>`` с ``\\n`` и ``%c``."""
        if source is not None:
            raw = str(getattr(source, "raw_text", "") or "")
            if raw:
                return raw
            text = str(getattr(source, "text", "") or "")
            if text:
                return text
        return str(fallback or "")

    def _engine_color_defs(self):
        value = self._color_defs
        if callable(value):
            try:
                value = value()
            except Exception:
                return {}
        return value or {}

    def refresh_color_suggestions(self):
        """Перечитать цвета из XML и обновить подсказку в редакторе."""
        try:
            self.description_label.set_color_suggestions(
                self._engine_color_defs()
            )
        except RuntimeError:
            pass

    @staticmethod
    def _text_source_editable(source):
        return (
            source is not None
            and getattr(source, "path", None) is not None
        )

    def _text_edit_predicate(self, source):
        """Право на правку названия/описания проверяется при клике."""

        def allowed():
            return self._text_source_editable(source) and bool(
                self._can_edit()
            )

        return allowed

    @staticmethod
    def _text_source_tooltip(text, source):
        path = getattr(source, "path", None)
        key = str(getattr(source, "key", "") or "")
        if path is None:
            return str(text or "")
        return "%s\n\nXML: %s\nid: %s" % (text, path, key)

    def set_saving(self, saving):
        """Заблокировать ввод и показать «часы» на время записи XML."""
        self._saving = bool(saving)
        if self._saving:
            self.name_label.end_editing()
            self.description_label.end_editing()
        widgets = (
            self.name_label,
            self.description_label,
            self.description_save_btn,
            self.fields_table,
            self.save_btn,
            self.open_doc_btn,
            self.open_tab_btn,
        )
        for widget in widgets:
            if widget is None:
                continue
            try:
                widget.setEnabled(not self._saving)
                if self._saving:
                    widget.setCursor(Qt.WaitCursor)
                else:
                    widget.unsetCursor()
            except RuntimeError:
                pass

        try:
            if self._saving:
                self.setCursor(Qt.WaitCursor)
            else:
                self.unsetCursor()
        except RuntimeError:
            pass

    def _apply_name_text(self):
        if self._saving or self._populating_text or self._entry is None:
            return

        value = self.name_label.text()
        self.name_label.end_editing()
        if value == self._shown_name:
            return

        if not self._can_edit() or self._on_text_edit is None:
            self.refresh()
            return

        if self._on_text_edit(self._entry.section, "name", value):
            self._shown_name = value
            QTimer.singleShot(0, self.refresh)
        else:
            self.refresh()

    def _on_description_editing_changed(self, editing):
        self.description_save_btn.setVisible(bool(editing))
        self.description_save_btn.setEnabled(
            bool(editing) and not self._saving
        )

    def _apply_description_text(self):
        if self._saving or self._populating_text or self._entry is None:
            return

        value = self.description_label.raw_text()
        self.description_label.end_editing()
        if value == self._shown_description:
            return

        if not self._can_edit() or self._on_text_edit is None:
            self.refresh()
            return

        if self._on_text_edit(
            self._entry.section,
            "description",
            value,
        ):
            self._shown_description = value
            QTimer.singleShot(0, self.refresh)
        else:
            self.refresh()

    def _clear_details(self):
        self._populating_text = True
        try:
            self.icon_label.clear()
            self.section_label.setText("—")
            self.name_label.clear()
            self.name_label.set_edit_allowed(False)
            self.meta_label.clear()
            self.description_label.clear()
            self.description_label.set_edit_allowed(False)
            self.description_save_btn.setVisible(False)
            self.description_panel.setVisible(False)
            self.fields_table.clearContents()
            self.fields_table.setRowCount(0)
            self._shown_name = ""
            self._shown_description = ""
        finally:
            self._populating_text = False

    # -------------------------------------------------- правка
    def _on_field_item_changed(self, item):
        if self._populating_fields or self._entry is None:
            return
        if not self._can_edit():
            # Проект закрыли уже после открытия редактора ячейки:
            # возвращаем отрисованное значение, чтобы правка не повисла.
            self.refresh()
            return

        row = item.row()
        if row < 0 or row >= len(self._entry.fields):
            return

        field = self._entry.fields[row]
        if field.line_number <= 0:
            return

        key_item = self.fields_table.item(row, 0)
        value_item = self.fields_table.item(row, 1)
        if key_item is None or value_item is None:
            return

        new_key = key_item.text().strip()
        new_value = value_item.text().strip()
        if not new_key:
            self.refresh()
            return

        if new_key == field.key and new_value == field.value:
            return

        new_line = "%s = %s" % (new_key, new_value)
        if new_key != field.key and self._on_field_rename is not None:
            if not self._on_field_rename(
                self._entry.section,
                field.key,
                new_key,
                new_value,
            ):
                self.refresh()
            else:
                QTimer.singleShot(0, self.refresh)
            return

        if new_key == field.key and self._on_field_edit is not None:
            if not self._on_field_edit(
                self._entry.section,
                new_key,
                new_value,
            ):
                self.refresh()
            else:
                QTimer.singleShot(0, self.refresh)
            return

        if self._on_replace_line is None:
            self.refresh()
            return
        if not self._on_replace_line(int(field.line_number), new_line):
            self.refresh()
        else:
            QTimer.singleShot(0, self.refresh)

    def _on_name_context_menu(self, position):
        if self.name_label.is_editing():
            menu = self.name_label.createStandardContextMenu()
        else:
            menu = self.name_label.createStandardContextMenu()
            self._add_open_xml_action(
                menu,
                getattr(self._entry, "name_source", None),
            )
        menu.exec_(self.name_label.mapToGlobal(position))

    def _on_description_context_menu(self, position):
        menu = self.description_label.createStandardContextMenu()
        if not self.description_label.is_editing():
            self._add_open_xml_action(
                menu,
                getattr(self._entry, "description_source", None),
            )
        menu.exec_(self.description_label.mapToGlobal(position))

    def _add_open_xml_action(self, menu, source):
        if source is None or getattr(source, "path", None) is None:
            return None
        menu.addSeparator()
        action = menu.addAction("Открыть XML")
        action.triggered.connect(
            lambda _checked=False: self._open_text_source(source)
        )
        return action

    def _open_text_source(self, source):
        if self._on_open_text_source is not None:
            self._on_open_text_source(source)

    def _text_source_for_field(self, field):
        """XML-объявление для текстового поля (``inv_name`` и т.п.)."""
        entry = self._entry
        if entry is None:
            return None

        key = str(getattr(field, "key", "") or "").strip().lower()
        if not key:
            return None

        sources = getattr(entry, "text_sources", None) or {}
        source = sources.get(key)
        if source is not None:
            return source

        # Совместимость с записями, собранными до появления text_sources.
        if key == "inv_name":
            return getattr(entry, "name_source", None)
        if key == "description":
            return getattr(entry, "description_source", None)
        return None

    def _on_fields_context_menu(self, position):
        index = self.fields_table.indexAt(position)
        if not index.isValid():
            return

        entry = self._entry
        row = index.row()
        if entry is None or row < 0 or row >= len(entry.fields):
            return

        field = entry.fields[row]
        line = field.line_number or entry.line_number
        editable = bool(self._can_edit())
        text_source = self._text_source_for_field(field)

        menu = QMenu(self)

        open_xml_action = None
        if text_source is not None:
            open_xml_action = menu.addAction("Открыть XML")
            menu.addSeparator()

        go_action = menu.addAction("Перейти к строке")
        go_action.setEnabled(line > 0)

        add_action = menu.addAction("Добавить строку под")
        add_action.setEnabled(editable and line > 0)

        delete_action = menu.addAction("Удалить строку")
        delete_action.setEnabled(editable and field.line_number > 0)

        chosen = menu.exec_(
            self.fields_table.mapToGlobal(position)
        )

        if chosen is open_xml_action and text_source is not None:
            self._open_text_source(text_source)
        elif chosen is go_action and line > 0:
            self._go_to_line(line)
        elif chosen is add_action and line > 0:
            self._add_line_under(entry, field, line)
        elif chosen is delete_action and field.line_number > 0:
            self._delete_selected_line(entry, field)

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
        if self._on_insert_line(int(line_number), text):
            self.refresh()

    def _delete_selected_line(self, entry, field):
        if self._on_delete_line is None:
            return
        if self._on_delete_line(int(field.line_number)):
            self.refresh()

    def _save(self):
        if self._on_save is not None:
            self._on_save()

    def _undo(self):
        if self._on_undo is not None:
            self._on_undo()

    def _redo(self):
        if self._on_redo is not None:
            self._on_redo()

    def undo(self):
        self._undo()

    def redo(self):
        self._redo()

    def _open_as_tab(self):
        if self._on_open_as_tab is not None:
            self._on_open_as_tab(self._section)

    def _open_document(self):
        if self._on_open_document is not None:
            self._on_open_document()

    # -------------------------------------------------- утилиты
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

    def _palette(self, name, fallback=None):
        try:
            value = self._theme_color(name, fallback)
        except Exception:
            value = fallback
        return value or fallback or "#787878"


class ItemViewerWindow(QDialog):
    """Немодальное окно: список предметов слева, поля и иконка справа."""

    def __init__(
        self,
        theme_color=None,
        color_defs=None,
        on_refresh=None,
        can_edit=None,
        on_replace_line=None,
        on_delete_line=None,
        on_insert_line=None,
        on_field_edit=None,
        on_field_rename=None,
        on_text_edit=None,
        on_go_to_line=None,
        on_save=None,
        on_create_item=None,
        on_item_entry=None,
        on_open_card=None,
        on_open_document=None,
        on_open_text_source=None,
        on_undo=None,
        on_redo=None,
        document_title="",
        parent=None,
    ):
        super().__init__(parent)

        self._theme_color = theme_color or (
            lambda name, fallback=None: fallback
        )
        self._color_defs = color_defs if color_defs is not None else {}
        self._on_refresh = on_refresh
        self._can_edit = can_edit or (lambda: False)
        self._on_replace_line = on_replace_line
        self._on_delete_line = on_delete_line
        self._on_insert_line = on_insert_line
        self._on_field_edit = on_field_edit
        self._on_field_rename = on_field_rename
        self._on_text_edit = on_text_edit
        self._on_go_to_line = on_go_to_line
        self._on_save = on_save
        self._on_create_item = on_create_item
        self._on_item_entry = on_item_entry
        self._on_open_card = on_open_card
        self._on_open_document = on_open_document
        self._on_open_text_source = on_open_text_source
        self._on_undo = on_undo
        self._on_redo = on_redo
        self._document_title = str(document_title or "")

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
        self.list_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_view.customContextMenuRequested.connect(
            self._on_item_context_menu
        )
        splitter.addWidget(self.list_view)

        self.card = ItemCardView(
            get_entry=self._live_entry,
            get_image=self._image_for,
            can_edit=self._can_edit,
            on_replace_line=self._on_replace_line,
            on_delete_line=self._on_delete_line,
            on_insert_line=self._on_insert_line,
            on_field_edit=self._on_field_edit,
            on_field_rename=self._on_field_rename,
            on_text_edit=self._on_text_edit,
            on_go_to_line=self._on_go_to_line,
            on_save=self._on_save,
            on_undo=self._on_undo,
            on_redo=self._on_redo,
            on_open_as_tab=self._open_current_card,
            on_open_document=None,
            on_open_text_source=self._on_open_text_source,
            theme_color=self._theme_color,
            color_defs=self._color_defs,
            document_title=self._document_title,
            preview=True,
        )
        splitter.addWidget(self.card)

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
        self.card.set_section(None)
        self._select_first_visible()

    def _first_entry(self):
        if self.proxy.rowCount() <= 0:
            return None
        return self._entry_from_index(self.proxy.index(0, 0))

    def _select_first_visible(self):
        entry = self._first_entry()
        if entry is None:
            self.card.set_section(None)
            return

        was_updating = self._updating
        self._updating = True
        self.list_view.setCurrentIndex(self.proxy.index(0, 0))
        self._updating = was_updating

        self._current_entry = entry
        self.card.set_section(entry.section)

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
                getattr(entry, "name_text", "") or "",
                getattr(entry, "description_text", "") or "",
            ]
        ).lower()

    # -------------------------------------------------- выбор
    def _on_item_context_menu(self, position):
        """ПКМ по предмету: открыть его карточку как вкладку."""
        if self._on_open_card is None:
            return

        viewport = self.list_view.viewport()
        viewport_pos = viewport.mapFrom(self.list_view, position)
        index = self.list_view.indexAt(viewport_pos)
        if not index.isValid():
            return

        entry = self._entry_from_index(index)
        if entry is None:
            return

        self.list_view.setCurrentIndex(index)

        menu = QMenu(self.list_view)
        open_action = menu.addAction("Открыть в новой вкладке")
        chosen = menu.exec_(viewport.mapToGlobal(viewport_pos))
        if chosen is open_action:
            self._open_current_card(entry.section)

    def _on_current_changed(self, current, _previous):
        if self._updating:
            return

        entry = self._entry_from_index(current)
        if entry is None:
            self._current_entry = None
            self.card.set_section(None)
            return

        if entry is self._current_entry:
            return

        self._current_entry = entry
        self.card.set_section(entry.section)

    def _live_entry(self):
        section = self.card.section()
        if self._on_item_entry is not None:
            return self._on_item_entry(section)
        return self._entry_for_section(section)

    def _entry_for_section(self, section):
        wanted = str(section or "").lower()
        for entry in self._entries:
            if entry.section.lower() == wanted:
                return entry
        return None

    def _image_for(self, section):
        return self._images.get(section)

    def _open_current_card(self, section):
        if self._on_open_card is not None:
            self._on_open_card(section)

    def update_section(self, section, entry):
        """Обновить одну строку каталога после изменения документа."""
        if entry is None:
            self._remove_section(section)
            return

        replaced = False
        for index, existing in enumerate(self._entries):
            if existing.section == entry.section:
                self._entries[index] = entry
                replaced = True
                break
        if not replaced:
            self._entries.append(entry)

        model_item = self._model_items.get(entry.section)
        if model_item is None:
            item = QStandardItem(
                QIcon(
                    self._gallery_pixmap(
                        self._images.get(entry.section)
                    )
                ),
                self._item_label(entry),
            )
            item.setEditable(False)
            item.setData(entry, ENTRY_ROLE)
            item.setData(self._search_text(entry), SEARCH_ROLE)
            item.setData(
                (entry.field("kind") or "").strip(),
                KIND_ROLE,
            )
            item.setToolTip(self._entry_tooltip(entry))
            self.model.appendRow(item)
            self._model_items[entry.section] = item
            model_item = item
        else:
            model_item.setData(entry, ENTRY_ROLE)
            model_item.setData(self._search_text(entry), SEARCH_ROLE)
            model_item.setData(
                (entry.field("kind") or "").strip(),
                KIND_ROLE,
            )
            model_item.setText(self._item_label(entry))
            model_item.setToolTip(self._entry_tooltip(entry))

        self.proxy.invalidateFilter()
        if (
            self._current_entry is not None
            and self._current_entry.section == entry.section
        ):
            self._current_entry = entry
            self.card.refresh(entry)

    def _remove_section(self, section):
        wanted = str(section or "").lower()
        if not wanted:
            return
        self._entries = [
            entry
            for entry in self._entries
            if entry.section.lower() != wanted
        ]
        model_item = self._model_items.pop(wanted, None)
        if model_item is None:
            for key, candidate in list(self._model_items.items()):
                if str(key).lower() == wanted:
                    model_item = self._model_items.pop(key)
                    break
        if model_item is not None:
            row = self.model.row(model_item)
            if row >= 0:
                self.model.removeRow(row)
        self.proxy.invalidateFilter()
        if (
            self._current_entry is not None
            and self._current_entry.section.lower() == wanted
        ):
            self._current_entry = None
            self.card.set_section(None)

    # -------------------------------------------------- фильтр / кнопки
    def showEvent(self, event):
        super().showEvent(event)
        self.card.refresh()

    def _request_save(self):
        if self._on_save is not None:
            self._on_save()

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
            self.card.set_section(None)
            return

        if entry is not previous:
            self._current_entry = entry
            self.card.set_section(entry.section)

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
