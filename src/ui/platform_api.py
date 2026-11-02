# -*- coding: utf-8 -*-
"""
PlatformApi — сервисы UI-платформы и точка регистраций для SDK.

Именно этот объект модуль видит как ctx.ui.
"""

from PyQt5.QtWidgets import QApplication, QFileDialog, QMessageBox

from sdk.events import Events

from .file_open_router import FileOpenRouter
from .input_bindings import MouseBindingManager, ShortcutManager
from .module_windows import ModuleWindowManager


class PlatformApi:
    def __init__(self, events, commands, tasks, services, paths):
        self.events = events
        self.commands = commands
        self.tasks = tasks
        self.services = services
        self.paths = paths

        self.router = FileOpenRouter(events=events)
        self._window_manager = ModuleWindowManager()
        self._shortcut_manager = ShortcutManager(self.trigger_action)
        self._mouse_manager = MouseBindingManager(self.trigger_action)

        self.window = None
        self._menu_reg = []      # (menu_title, label, callback, shortcut, action)
        self._ctx_actions = []   # (label, handler(path))
        self._log_buffer = []

        commands.register("ui.open_file", self.request_open_file)
        commands.register("ui.open_file_raw", self.request_open_file_raw)
        commands.register("ui.editor_undo", self.request_editor_undo)
        commands.register("ui.editor_redo", self.request_editor_redo)
        commands.register("ui.editor_find", self.request_editor_find)
        commands.register("ui.open_file_at_line", self.request_open_file_at_line)  # ← новое


    # -------------------------------------------------- регистрации (SDK)
    def register_menu(self, menu_title, label, callback, shortcut=None, action=None):
        self._menu_reg.append((menu_title, label, callback, shortcut, action))

    def register_tree_context_action(self, label, handler):
        self._ctx_actions.append((label, handler))

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
    def show_text(self, text, label, readonly=True):
        if self.window is not None:
            self.window.editor.show_text(text, label, readonly=readonly)
        else:
            self._log_buffer.append("[show_text до окна] %s" % label)

    def request_open_file_raw(self, path):
        ok = self.router.open_default(path, self)
        if not ok:
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
        """Открыть файл и перейти к указанной строке."""
        ok = self.router.open(path, self)
        if not ok:
            self.show_error("Не найден обработчик для открытия файла: %s" % path)
            return ok
        
        # Переходим к строке если окно — редактор
        if self.window is not None and self.window.editor.current_file == str(path):
            self.window.editor.goto_line(line_number)
        
        return ok
