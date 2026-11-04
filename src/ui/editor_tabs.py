# -*- coding: utf-8 -*-
"""Вкладки редактора: базовый редактор + вкладки, созданные модулями."""

import logging
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QMenu, QTabWidget, QWidget

from sdk.events import Events

from .editor import CodeEditor


log = logging.getLogger(__name__)
DEFAULT_TAB_ID = "ui.editor"


class EditorTabs(QTabWidget):
    """Контейнер вкладок. Содержимое вкладок создают модули."""

    def __init__(self, events=None, commands=None, parent=None):
        super().__init__(parent)

        self._events = events
        self._commands = commands
        self._tab_ids = {}
        self._tab_source_paths = {}
        self._default_editor = None

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
        self._default_editor = editor
        self._add_tab(DEFAULT_TAB_ID, "Редактор", editor)
        return editor

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
            self._publish_file_opened(source_path)
            return

        if existing is not None:
            self.close_tab(tab_id)

        self._add_tab(tab_id, title, widget)
        self._tab_source_paths[tab_id] = source_path
        self._publish(Events.UI_TAB_OPENED, tab_id)
        self._publish_file_opened(source_path)

    def close_tab(self, tab_id):
        widget = self._tab_ids.pop(tab_id, None)
        self._tab_source_paths.pop(tab_id, None)
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

    @property
    def current_tab_id(self):
        return self._tab_id_for_widget(self.currentWidget())

    # -------------------------------------------------- совместимость с CodeEditor
    @property
    def current_file(self):
        editor = self._active_editor()
        return editor.current_file if editor is not None else None

    def open_file(self, file_path):
        path = Path(file_path)
        editor = self._ensure_default_tab()
        self.setCurrentWidget(editor)
        editor.open_file(file_path)

        if path.is_file():
            self._present_default_tab(path.name, str(path), path)

    def show_text(self, text, label, readonly=True, source_path=None):
        editor = self._ensure_default_tab()
        self.setCurrentWidget(editor)
        editor.show_text(
            text,
            label,
            readonly=readonly,
            source_path=source_path,
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
            source_path if source_path is not None else tooltip
        )

    def goto_line(self, line_number):
        editor = self._active_editor()
        if editor is not None:
            editor.goto_line(line_number)

    def undo(self):
        editor = self._active_editor()
        if editor is not None:
            editor.undo()

    def redo(self):
        editor = self._active_editor()
        if editor is not None:
            editor.redo()

    def show_find(self):
        editor = self._active_editor()
        if editor is not None:
            editor.show_find()

    # -------------------------------------------------- служебное
    def _add_tab(self, tab_id, title, widget):
        index = self.addTab(widget, title)
        self._tab_ids[tab_id] = widget
        self.setCurrentIndex(index)
        return widget

    def _active_editor(self):
        widget = self.currentWidget()
        return widget if isinstance(widget, CodeEditor) else None

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
            self._run_file_action(file_actions[chosen], path)
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
        """Действия с файлом вкладки: те же команды, что и в дереве VFS."""
        if path is None or self._commands is None:
            return {}

        suffix = Path(path).suffix.lower()
        resolved_external = (
            "base.open_xml_resolved_notepad"
            if suffix == ".xml"
            else "base.assemble_open"
        )

        entries = (
            ("Открыть исходный", "base.open_raw"),
            ("Открыть resolved", "ui.open_file"),
            ("Открыть resolved в Notepad++", resolved_external),
            ("Открыть исходный в Notepad++", "base.open_notepad"),
        )

        actions = {}
        for label, command in entries:
            if not self._has_command(command):
                continue
            actions[menu.addAction(label)] = command
        return actions

    def _has_command(self, name):
        has = getattr(self._commands, "has", None)
        return bool(has(name)) if callable(has) else True

    def _run_file_action(self, command, path):
        try:
            self._commands.execute(command, path)
        except Exception:
            log.exception("Не удалось выполнить команду вкладки: %s", command)

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

    def _publish_file_opened(self, path):
        if path is not None:
            self._publish(Events.FILE_OPENED, str(path))


def _short_title(text, limit=40):
    text = str(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
