# -*- coding: utf-8 -*-
"""Вкладки редактора: базовый редактор + вкладки, созданные модулями."""

from pathlib import Path

from PyQt5.QtWidgets import QTabWidget, QWidget

from sdk.events import Events

from .editor import CodeEditor


DEFAULT_TAB_ID = "ui.editor"


class EditorTabs(QTabWidget):
    """Контейнер вкладок. Содержимое вкладок создают модули."""

    def __init__(self, events=None, parent=None):
        super().__init__(parent)

        self._events = events
        self._tab_ids = {}
        self._default_editor = None

        self.setTabsClosable(True)
        self.tabCloseRequested.connect(self._on_close_requested)
        self.currentChanged.connect(self._on_current_changed)

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
            self._publish(Events.UI_TAB_OPENED, tab_id)
            self._publish_file_opened(source_path)
            return

        if existing is not None:
            self.close_tab(tab_id)

        self._add_tab(tab_id, title, widget)
        self._publish(Events.UI_TAB_OPENED, tab_id)
        self._publish_file_opened(source_path)

    def close_tab(self, tab_id):
        widget = self._tab_ids.pop(tab_id, None)
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
