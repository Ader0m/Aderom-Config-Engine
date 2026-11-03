# -*- coding: utf-8 -*-
"""
PlatformApi — сервисы UI-платформы и точка регистраций для SDK.

Именно этот объект модуль видит как ctx.ui.
"""

import os

from PyQt5.QtWidgets import QApplication, QFileDialog, QMessageBox

from sdk.events import Events

from .file_open_router import FileOpenRouter
from .input_bindings import MouseBindingManager, ShortcutManager
from .module_windows import ModuleWindowManager


def _path_key(path):
    """Нормализовать путь для сопоставления запроса открытия и события."""
    return os.path.normcase(os.path.abspath(os.fspath(path)))


class PlatformApi:
    def __init__(self, events, commands, tasks, services, paths):
        self.events = events
        self.commands = commands
        self.tasks = tasks
        self.services = services
        self.paths = paths

        self.router = FileOpenRouter()
        self._window_manager = ModuleWindowManager()
        self._shortcut_manager = ShortcutManager(self.trigger_action)
        self._mouse_manager = MouseBindingManager(self.trigger_action)

        self.window = None
        self._menu_reg = []      # (menu_title, label, callback, shortcut, action)
        self._ctx_actions = []   # (label, handler(path), predicate(path)|None)
        self._log_buffer = []

        # Отложенная навигация: ключ — нормализованный путь файла.
        # Запрос живёт до события FILE_OPENED ровно для этого пути.
        self._pending_file_actions = {}

        self._file_opened_sub = events.subscribe(
            Events.FILE_OPENED,
            self._on_file_opened,
        )
        self._file_open_failed_sub = events.subscribe(
            Events.FILE_OPEN_FAILED,
            self._on_file_open_failed,
        )

        commands.register("ui.open_file", self.request_open_file)
        commands.register("ui.open_file_raw", self.request_open_file_raw)
        commands.register("ui.open_file_raw_at_line", self.request_open_file_raw_at_line)
        commands.register("ui.editor_undo", self.request_editor_undo)
        commands.register("ui.editor_redo", self.request_editor_redo)
        commands.register("ui.editor_find", self.request_editor_find)
        commands.register("ui.open_file_at_line", self.request_open_file_at_line)
        commands.register("ui.open_file_with_search", self.request_open_file_with_search)


    # -------------------------------------------------- регистрации (SDK)
    def register_menu(self, menu_title, label, callback, shortcut=None, action=None):
        self._menu_reg.append((menu_title, label, callback, shortcut, action))

    def register_tree_context_action(self, label, handler, predicate=None):
        """Добавить действие в контекстное меню дерева.

        ``predicate(path)`` — необязательный фильтр: действие показывается
        только для путей, для которых он вернул truthy.
        """
        self._ctx_actions.append((label, handler, predicate))

    def register_shortcut(self, key, callback, scope="global", action=None):
        self._shortcut_manager.register(key, callback, scope=scope, action=action)

    def register_mouse_binding(
        self,
        button,
        modifiers,
        callback,
        scope="global",
        event="press",
        action=None,
    ):
        self._mouse_manager.register(
            button,
            modifiers,
            callback,
            scope=scope,
            event=event,
            action=action,
        )

    def register_action_handler(self, action, handler, priority=0):
        return self.events.subscribe(action, handler, priority)

    def dispatch_action(self, action, payload=None):
        return self.events.dispatch(action, payload)

    def trigger_action(self, action, callback, payload=None, fallback=None):
        """Выполнить UI-действие с возможностью перехвата.

        Сначала рассылается конкретное ``action``, затем общее действие
        ``fallback`` (например, ``ui.shortcut``). Если любое из них
        перехвачено, ``callback`` не вызывается.
        """
        if action and self.events.dispatch(action, payload):
            return True
        if fallback and self.events.dispatch(fallback, payload):
            return True
        result = callback()
        return bool(result)

    @property
    def menu_registrations(self):
        return list(self._menu_reg)

    @property
    def context_actions(self):
        return list(self._ctx_actions)

    # -------------------------------------------------- привязка окна
    def bind_window(self, window):
        self.window = window
        self._window_manager.bind_main_window(window)
        self._shortcut_manager.attach(window)
        self._mouse_manager.attach(window)
        buf, self._log_buffer = self._log_buffer, []
        for m in buf:
            window.log(m)

    # -------------------------------------------------- ui-сервисы
    def log(self, msg):
        if self.window is not None:
            self.window.log(msg)
        else:
            self._log_buffer.append(str(msg))

    def set_status(self, msg, timeout=0):
        if self.window is not None:
            self.window.statusBar().showMessage(str(msg), timeout)

    def show_error(self, text):
        if self.window is not None:
            QMessageBox.critical(self.window, "Ошибка", str(text))
        else:
            print(text)

    def ask_directory(self, initial=""):
        d = QFileDialog.getExistingDirectory(
            self.window,
            "Выбрать папку с конфигами",
            initial or "",
            QFileDialog.ShowDirsOnly,
        )
        return d or None

    def clipboard_set(self, text):
        QApplication.clipboard().setText(str(text))

    # -------------------------------------------------- команды платформы
    def request_open_file(self, path):
        ok = self.router.open(path, self)
        if not ok:
            self.show_error("Не найден обработчик для открытия файла: %s" % path)
        return ok

    # -------------------------------------------------- регистрации (SDK)
    def register_open_handler(self, handler):
        """Модуль регистрирует своего обработчика открытия файла."""
        self.router.register(handler)

    # -------------------------------------------------- окна модулей
    def show_module_window(self, key, window):
        """Показать окно, созданное модулем."""
        self._window_manager.show(key, window)
        self.events.publish(Events.UI_WINDOW_SHOWN, key)

    def close_module_window(self, key):
        """Закрыть окно модуля по ключу."""
        self._window_manager.close(key)
        self.events.publish(Events.UI_WINDOW_CLOSED, key)

    # -------------------------------------------------- вкладки редактора
    def open_editor_tab(self, tab_id, title, widget):
        """Открыть вкладку, созданную модулем."""
        if self.window is None:
            self.log("[open_editor_tab до окна] %s" % tab_id)
            return
        self.window.editor.open_tab(tab_id, title, widget)

    def close_editor_tab(self, tab_id):
        """Закрыть вкладку модуля."""
        if self.window is None:
            return
        self.window.editor.close_tab(tab_id)

    # -------------------------------------------------- ui-сервисы
    def show_text(self, text, label, readonly=True, source_path=None):
        if self.window is not None:
            self.window.editor.show_text(
                text,
                label,
                readonly=readonly,
                source_path=source_path,
            )
        else:
            self._log_buffer.append("[show_text до окна] %s" % label)

    def request_open_file_raw(self, path):
        ok = self.router.open_default(path, self)
        if not ok:
            self.show_error("Нет обработчика по умолчанию для: %s" % path)
        return ok

    def request_open_file_raw_at_line(self, path, line_number: int):
        """Открыть исходный файл (без резолва) и перейти к строке."""
        key = _path_key(path)
        self._pending_file_actions[key] = ("line", int(line_number))

        ok = self.router.open_default(path, self)
        if not ok:
            self._pending_file_actions.pop(key, None)
            self.show_error("Нет обработчика по умолчанию для: %s" % path)
        return ok

    def request_editor_undo(self):
        if self.window is not None:
            self.window.editor.undo()

    def request_editor_redo(self):
        if self.window is not None:
            self.window.editor.redo()

    def request_editor_find(self):
        if self.window is not None:
            self.window.editor.show_find()

    def ask_text(self, prompt: str, initial: str = "") -> str:
        """Запросить текст у пользователя через диалог."""
        from PyQt5.QtWidgets import QInputDialog
        
        text, ok = QInputDialog.getText(
            self.window,
            "Ввод",
            prompt,
            text=initial,
        )
        return text if ok and text else ""

    def request_open_file_at_line(self, path, line_number: int):
        """Открыть файл и перейти к указанной строке.

        Навигация применяется не сразу: файл может открываться асинхронно
        (резолв .ltx). Запрос ждёт события :attr:`Events.FILE_OPENED` для
        этого же пути.
        """
        key = _path_key(path)
        self._pending_file_actions[key] = ("line", int(line_number))

        ok = self.router.open(path, self)
        if not ok:
            self._pending_file_actions.pop(key, None)
            self.show_error("Не найден обработчик для открытия файла: %s" % path)
        return ok

    def request_open_file_with_search(self, path, search_text):
        """Открыть файл и перейти к первому вхождению ``search_text``.

        Навигация откладывается до факта открытия именно этого пути:
        для резолва .ltx виджет создаётся позже, после фоновой сборки.
        """
        key = _path_key(path)
        self._pending_file_actions[key] = ("search", search_text)

        ok = self.router.open(path, self)
        if not ok:
            self._pending_file_actions.pop(key, None)
            self.show_error("Не найден обработчик для: %s" % path)
        return ok

    # -------------------------------------------------- факты открытия
    def _on_file_opened(self, path):
        """Событие FILE_OPENED: применить навигацию ровно для этого пути."""
        pending = self._pending_file_actions.pop(_path_key(path), None)
        if pending is None or self.window is None:
            return

        kind, value = pending
        widget = self.window.editor.currentWidget()

        if kind == "search":
            if widget is not None and hasattr(widget, "find_first"):
                if value and not widget.find_first(value):
                    self.set_status(
                        "Владелец открыт, но строка не найдена: %s" % value,
                        8000,
                    )
            else:
                self.log(
                    "[open_file_with_search] активная вкладка не поддерживает "
                    "поиск: %s" % type(widget).__name__
                )
        elif kind == "line":
            if widget is not None and hasattr(widget, "goto_line"):
                widget.goto_line(value)
            else:
                self.log(
                    "[open_file_at_line] активная вкладка не поддерживает "
                    "переход к строке: %s" % type(widget).__name__
                )

    def _on_file_open_failed(self, path):
        """Файл не открылся: отложенная навигация больше не нужна."""
        if self._pending_file_actions.pop(_path_key(path), None) is not None:
            self.log("[open] навигация отменена, файл не открыт: %s" % path)

    def open_resolved_tab(self, tab_id, title, text, line_sources, root_path,
                          line_number=None):
        """Создать ResolvedView и открыть его как вкладку."""
        if self.window is None:
            return
        from .editor import ResolvedView  # локальный импорт, чтобы не цеплять PyQt до окна

        view = ResolvedView()
        view.set_content(text, line_sources, root_path)
        view.source_activated.connect(
            lambda path, search: self.request_open_file_with_search(path, search)
        )
        view.diagnostic.connect(lambda msg: self.log("[resolved] %s" % msg))
        view.diagnostic.connect(lambda msg: self.set_status(msg, 8000))
        self.window.editor.open_tab(
            tab_id,
            title,
            view,
            source_path=root_path,
        )
        if line_number is not None:
            view.goto_line(int(line_number))

    def open_xml_resolved_tab(self, tab_id, title, text, root_path,
                              line_sources=None):
        """Открыть XML-резолв в отдельной вкладке с панелью источников."""
        if self.window is None:
            return
        from .editor import ResolvedView

        sources = list(line_sources or [])
        has_sources = any(src is not None for src in sources)
        view = ResolvedView()
        view.set_content(
            text,
            sources,
            str(root_path),
            sources_visible=has_sources,
        )
        view.source_activated.connect(
            lambda path, search: self.request_open_file_with_search(path, search)
        )
        view.source_raw_activated.connect(
            lambda path, search: self.request_open_file_raw_with_search(path, search)
        )
        view.diagnostic.connect(lambda msg: self.log("[xml-resolved] %s" % msg))
        view.diagnostic.connect(lambda msg: self.set_status(msg, 8000))
        self.window.editor.open_tab(
            tab_id,
            title,
            view,
            source_path=str(root_path),
        )

    def request_open_file_raw_with_search(self, path, search_text):
        """Открыть файл как сырой текст и перейти к строке поиска."""
        key = _path_key(path)
        self._pending_file_actions[key] = ("search", search_text)

        ok = self.router.open_default(path, self)
        if not ok:
            self._pending_file_actions.pop(key, None)
            self.show_error("Нет обработчика по умолчанию для: %s" % path)
        return ok
