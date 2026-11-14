# -*- coding: utf-8 -*-
"""Вкладки редактора: базовый редактор + вкладки, созданные модулями."""

import logging
from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QPainter, QPalette, QPen, QPixmap
from PyQt5.QtWidgets import QMenu, QTabWidget, QWidget

from sdk.api import FileOpened
from sdk.events import Events

from .editor import CodeEditor


log = logging.getLogger(__name__)
DEFAULT_TAB_ID = "ui.editor"


class EditorTabs(QTabWidget):
    """Контейнер вкладок. Содержимое вкладок создают модули."""

    document_changed = pyqtSignal(str, str)
    save_requested = pyqtSignal(str, str)
    lexer_status = pyqtSignal(str)

    def __init__(self, events=None, commands=None, parent=None, can_edit=None,
                 file_actions=None):
        super().__init__(parent)

        self._events = events
        self._commands = commands
        self._file_actions = file_actions or (lambda: ())
        self._tab_ids = {}
        self._tab_source_paths = {}
        self._default_editor = None
        self._can_edit = can_edit or (lambda: False)
        # Иконка «часики» на время загрузки проекта: редактирование в
        # это время запрещено, а после разблокировки иконка снимается.
        self._busy = False
        self._busy_icon_cache = None
        self._busy_icons = {}
        # Вкладки, которые остаются неготовыми после загрузки проекта
        # (например, осиротевший resolved-документ). Их «часики» не
        # снимаются общей разблокировкой.
        self._tab_busy = set()

        self.setTabsClosable(True)
        self.tabCloseRequested.connect(self._on_close_requested)
        self.currentChanged.connect(self._on_current_changed)

        tab_bar = self.tabBar()
        tab_bar.setContextMenuPolicy(Qt.CustomContextMenu)
        tab_bar.customContextMenuRequested.connect(self._on_tab_context_menu)

        self._ensure_default_tab()

    # -------------------------------------------------- базовая вкладка
    def _ensure_default_tab(self):
        if self._default_editor is not None:
            return self._default_editor

        editor = CodeEditor()
        editor.set_editable(self._can_edit())
        editor.content_changed.connect(
            lambda text, e=editor: self._on_editor_changed(e, text)
        )
        editor.save_requested.connect(
            lambda e=editor: self._on_editor_save(e)
        )
        editor.dirty_changed.connect(
            lambda dirty, e=editor: self._on_editor_dirty(e, dirty)
        )
        editor.lexer_status.connect(self.lexer_status.emit)
        self._default_editor = editor
        self._add_tab(DEFAULT_TAB_ID, "Редактор", editor)
        return editor

    # -------------------------------------------------- проект и черновик
    def _on_editor_changed(self, editor, text):
        path = editor.current_file
        if path:
            self.document_changed.emit(str(path), text)

    def _on_editor_save(self, editor):
        path = editor.current_file
        if path:
            self.save_requested.emit(str(path), editor.text())

    def _on_editor_dirty(self, editor, dirty):
        tab_id = self._tab_id_for_widget(editor)
        if tab_id is not None:
            self.set_tab_dirty(tab_id, dirty)

    def set_tab_dirty(self, tab_id, dirty):
        widget = self._tab_ids.get(tab_id)
        if widget is None:
            return
        index = self.indexOf(widget)
        if index < 0:
            return

        title = self.tabText(index)
        if dirty:
            if not title.startswith("* "):
                self.setTabText(index, "* " + title)
        else:
            if title.startswith("* "):
                self.setTabText(index, title[2:])

    def mark_tab_clean(self, tab_id):
        widget = self._tab_ids.get(tab_id)
        marker = getattr(widget, "mark_clean", None)
        if callable(marker):
            marker()
        self.set_tab_dirty(tab_id, False)

    def set_editable(self, editable):
        """Применить режим редактирования ко всем текстовым вкладкам."""
        for widget in self._tab_ids.values():
            setter = getattr(widget, "set_editable", None)
            if callable(setter):
                setter(editable)

    def set_busy(self, busy):
        """Показать/снять «часики» загрузки проекта на всех вкладках.

        Иконка — только индикатор: сам запрет правки задаётся через
        :meth:`set_editable`. Вкладки, открытые во время загрузки, тоже
        получают часики. Индивидуально помеченные вкладки
        (:meth:`set_tab_busy`) сохраняют иконку после общей разблокировки.
        """
        busy = bool(busy)
        if busy == self._busy:
            return
        self._busy = busy
        for index in range(self.count()):
            self._apply_busy(index)
        if not busy:
            for tab_id in list(self._busy_icons):
                if tab_id not in self._tab_busy:
                    self._busy_icons.pop(tab_id, None)

    @property
    def is_busy(self):
        return self._busy

    def set_tab_busy(self, tab_id, busy):
        """Индивидуальные «часики»: вкладка ещё не готова к правке."""
        tab_id = str(tab_id or "")
        widget = self._tab_ids.get(tab_id)
        if widget is None:
            return False

        if busy:
            self._tab_busy.add(tab_id)
        else:
            self._tab_busy.discard(tab_id)

        index = self.indexOf(widget)
        if index >= 0:
            self._apply_busy(index)
        return True

    def is_tab_busy(self, tab_id):
        return str(tab_id or "") in self._tab_busy

    def clear_tab_busy(self):
        """Снять индивидуальные «часики» со всех вкладок."""
        for tab_id in list(self._tab_busy):
            self.set_tab_busy(tab_id, False)

    def _apply_busy(self, index):
        widget = self.widget(index)
        if widget is None:
            return
        tab_id = self._tab_id_for_widget(widget)
        if tab_id is None:
            return

        busy = self._busy or tab_id in self._tab_busy
        if busy:
            if tab_id not in self._busy_icons:
                self._busy_icons[tab_id] = self.tabIcon(index)
            self.setTabIcon(index, self._busy_icon())
            return

        previous = self._busy_icons.pop(tab_id, None)
        self.setTabIcon(
            index,
            previous if previous is not None else QIcon(),
        )

    def _busy_icon(self):
        if self._busy_icon_cache is not None:
            return self._busy_icon_cache

        size = 16
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)

        painter = QPainter(pixmap)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            color = self.palette().color(QPalette.WindowText)
            painter.setPen(QPen(color, 1.6))
            painter.drawEllipse(2, 2, size - 4, size - 4)
            center = size // 2
            painter.drawLine(center, center, center, 4)
            painter.drawLine(center, center, size - 5, center + 2)
        finally:
            painter.end()

        self._busy_icon_cache = QIcon(pixmap)
        return self._busy_icon_cache

    def mark_clean(self, path, text=None):
        """Снять признак несохранённых изменений у вкладки файла."""
        path = str(path)
        for widget in self._tab_ids.values():
            if not isinstance(widget, CodeEditor):
                continue
            if str(widget.current_file) != path:
                continue
            if text is not None and widget.text() != str(text):
                continue
            widget.mark_clean(text)

    def reload_path(self, path, text):
        """Перечитать открытую вкладку файла из проекта."""
        path = str(path)
        for widget in self._tab_ids.values():
            if not isinstance(widget, CodeEditor):
                continue
            if str(widget.current_file) != path:
                continue
            widget.open_file(path, text=text)

    def current_editor(self):
        widget = self.currentWidget()
        return widget if isinstance(widget, CodeEditor) else None

    def open_paths(self):
        """Пути, открытые в текстовых вкладках."""
        result = []
        for widget in self._tab_ids.values():
            if isinstance(widget, CodeEditor) and widget.current_file:
                result.append(widget.current_file)
        return result

    def resolved_tabs(self):
        """Открытые resolved-вкладки: ``(tab_id, widget)``."""
        result = []
        for tab_id, widget in self._tab_ids.items():
            if hasattr(widget, "root_path") and hasattr(
                widget,
                "line_sources",
            ):
                result.append((tab_id, widget))
        return result

    # -------------------------------------------------- вкладки модулей
    def open_tab(self, tab_id, title, widget, source_path=None):
        """Открыть вкладку модуля и опубликовать факт её размещения.

        ``source_path`` — файл, который показывает вкладка. Он попадает в
        событие :attr:`Events.FILE_OPENED`; по нему платформа понимает,
        к какому именно открытию относится отложенная навигация.
        """
        if not tab_id:
            raise ValueError("tab_id не может быть пустым")
        if tab_id == DEFAULT_TAB_ID:
            raise ValueError("tab_id '%s' зарезервирован" % DEFAULT_TAB_ID)
        if not isinstance(widget, QWidget):
            raise TypeError("Содержимое вкладки должно быть QWidget")

        existing = self._tab_ids.get(tab_id)
        if existing is widget:
            # Виджет переиспользуется, но вкладка всё равно становится
            # текущей — это тоже состоявшееся открытие.
            index = self.indexOf(widget)
            self.setTabText(index, title)
            self.setCurrentIndex(index)
            self._tab_source_paths[tab_id] = source_path
            self._publish(Events.UI_TAB_OPENED, tab_id)
            self._publish_file_opened(source_path, tab_id)
            return

        if existing is not None:
            self.close_tab(tab_id)

        self._add_tab(tab_id, title, widget)
        self._tab_source_paths[tab_id] = source_path
        self._publish(Events.UI_TAB_OPENED, tab_id)
        self._publish_file_opened(source_path, tab_id)

    def flush_pending(self):
        """Зафиксировать отложенный текст всех вкладок."""
        for widget in list(self._tab_ids.values()):
            flush = getattr(widget, "flush_pending", None)
            if callable(flush):
                try:
                    flush()
                except Exception:
                    log.exception("Не удалось зафиксировать текст вкладки")

    def close_tab(self, tab_id):
        widget = self._tab_ids.get(tab_id)
        flush = getattr(widget, "flush_pending", None)
        if callable(flush):
            try:
                flush()
            except Exception:
                log.exception("Не удалось зафиксировать текст вкладки")
        self._tab_ids.pop(tab_id, None)
        self._tab_source_paths.pop(tab_id, None)
        self._tab_busy.discard(tab_id)
        self._busy_icons.pop(tab_id, None)
        if widget is None:
            return False

        if widget is self._default_editor:
            # Вкладка закрывается как обычная, но при следующем
            # open_file/show_text _ensure_default_tab создаст свежий
            # редактор и снова добавит его вкладкой.
            self._default_editor = None

        index = self.indexOf(widget)
        if index != -1:
            self.removeTab(index)
        widget.deleteLater()
        self._publish(Events.UI_TAB_CLOSED, tab_id)
        return True

    def has_tab(self, tab_id):
        return tab_id in self._tab_ids

    def get_tab(self, tab_id):
        """Виджет вкладки по id или ``None``."""
        return self._tab_ids.get(tab_id)

    @property
    def current_tab_id(self):
        return self._tab_id_for_widget(self.currentWidget())

    # -------------------------------------------------- совместимость с CodeEditor
    @property
    def current_file(self):
        editor = self._active_editor()
        return editor.current_file if editor is not None else None

    def open_file(self, file_path, text=None):
        path = Path(file_path)
        editor = self._ensure_default_tab()
        self.setCurrentWidget(editor)
        editor.set_editable(self._can_edit())
        editor.open_file(file_path, text=text)

        if path.is_file() or text is not None:
            self._present_default_tab(path.name, str(path), path)

    def show_text(self, text, label, readonly=True, source_path=None,
                  line_number=None, syntax=True, notice=None):
        editor = self._ensure_default_tab()
        self.setCurrentWidget(editor)
        editor.show_text(
            text,
            label,
            readonly=readonly,
            source_path=source_path,
            line_number=line_number,
            syntax=syntax,
            notice=notice,
        )

        title = str(label)
        self._present_default_tab(
            _short_title(title),
            title,
            source_path,
        )

    def _present_default_tab(self, tab_text, tooltip, source_path):
        """Обновить заголовок дефолтной вкладки и опубликовать открытие."""
        index = self.indexOf(self._default_editor)
        self.setTabText(index, tab_text)
        self.setTabToolTip(index, tooltip)
        self._tab_source_paths[DEFAULT_TAB_ID] = source_path
        self._publish(Events.UI_TAB_OPENED, DEFAULT_TAB_ID)
        self._publish_file_opened(
            source_path if source_path is not None else tooltip,
            DEFAULT_TAB_ID,
        )

    def goto_line(self, line_number):
        widget = self._active_widget()
        goto = getattr(widget, "goto_line", None)
        if callable(goto):
            goto(line_number)

    def undo(self):
        widget = self._active_widget()
        undo = getattr(widget, "undo", None)
        if callable(undo):
            undo()

    def redo(self):
        widget = self._active_widget()
        redo = getattr(widget, "redo", None)
        if callable(redo):
            redo()

    def show_find(self):
        widget = self._active_widget()
        find = getattr(widget, "show_find", None)
        if callable(find):
            find()

    # -------------------------------------------------- служебное
    def _add_tab(self, tab_id, title, widget):
        index = self.addTab(widget, title)
        self._tab_ids[tab_id] = widget
        self.setCurrentIndex(index)
        if self._busy:
            self._apply_busy(index)
        return widget

    def _active_editor(self):
        widget = self.currentWidget()
        return widget if isinstance(widget, CodeEditor) else None

    def _active_widget(self):
        return self.currentWidget()

    def _tab_id_for_widget(self, widget):
        for tab_id, candidate in self._tab_ids.items():
            if candidate is widget:
                return tab_id
        return None

    # -------------------------------------------------- контекстное меню вкладок
    def _on_tab_context_menu(self, pos):
        tab_bar = self.tabBar()
        index = tab_bar.tabAt(pos)
        if index < 0:
            return

        # Действия считаются относительно вкладки, по которой кликнули.
        self.setCurrentIndex(index)

        menu = QMenu(self)

        path = self._tab_path(index)
        file_actions = self._add_file_actions(menu, path)
        if file_actions:
            menu.addSeparator()

        close_right = menu.addAction("Закрыть все вкладки справа")
        close_right.setEnabled(index + 1 < self.count())

        close_others = menu.addAction("Закрыть все другие вкладки")
        close_others.setEnabled(self.count() > 1)

        menu.addSeparator()

        close_all = menu.addAction("Закрыть все вкладки")

        chosen = menu.exec_(tab_bar.mapToGlobal(pos))

        if chosen in file_actions:
            try:
                file_actions[chosen](path)
            except Exception:
                log.exception("Не удалось выполнить файловое действие")
        elif chosen is close_right:
            self._close_tabs_by_indices(
                range(index + 1, self.count())
            )
        elif chosen is close_others:
            self._close_tabs_by_indices(
                i for i in range(self.count()) if i != index
            )
        elif chosen is close_all:
            self._close_tabs_by_indices(range(self.count()))

    def _tab_path(self, index):
        """Файл, который показывает вкладка (если известен)."""
        widget = self.widget(index)
        tab_id = self._tab_id_for_widget(widget)

        path = self._tab_source_paths.get(tab_id)
        if path is None and isinstance(widget, CodeEditor):
            path = widget.current_file
        return str(path) if path is not None else None

    def _add_file_actions(self, menu, path):
        """Действия с файлом вкладки из общего реестра платформы.

        Платформа не знает имён команд конкретного модуля: модули сами
        регистрируют действия через ``ctx.ui.register_file_action``.
        """
        if path is None:
            return {}

        actions = {}
        for entry in self._file_actions() or ():
            try:
                label, handler = entry[0], entry[1]
                predicate = entry[2] if len(entry) > 2 else None
            except (TypeError, IndexError):
                log.warning("Некорректная запись file action: %r", entry)
                continue
            try:
                if predicate is not None and not predicate(path):
                    continue
            except Exception:
                log.exception(
                    "Ошибка predicate файлового действия: %s", label
                )
                continue

            action = menu.addAction(label)
            actions[action] = handler

        return actions

    def _close_tabs_by_indices(self, indices):
        """Закрыть вкладки по индексам, не сбивая нумерацию при удалении."""
        tab_ids = []
        for index in indices:
            widget = self.widget(index)
            tab_id = self._tab_id_for_widget(widget)
            if tab_id is not None:
                tab_ids.append(tab_id)

        for tab_id in tab_ids:
            self.close_tab(tab_id)

    def _on_close_requested(self, index):
        widget = self.widget(index)
        tab_id = self._tab_id_for_widget(widget)
        if tab_id is not None:
            self.close_tab(tab_id)

    def _on_current_changed(self, _index):
        self._publish(Events.UI_TAB_CHANGED, self.current_tab_id)

    def _publish(self, name, payload):
        if self._events is not None:
            self._events.publish(name, payload)

    def _publish_file_opened(self, path, tab_id=""):
        if path is not None:
            self._publish(
                Events.FILE_OPENED,
                FileOpened(path=Path(path), tab_id=str(tab_id or "")),
            )


def _short_title(text, limit=40):
    text = str(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
