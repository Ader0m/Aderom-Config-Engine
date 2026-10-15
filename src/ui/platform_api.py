# -*- coding: utf-8 -*-
"""
PlatformApi — сервисы UI-платформы и точка регистраций для SDK.

Именно этот объект модуль видит как ctx.ui.
"""

from PyQt5.QtWidgets import QApplication, QFileDialog, QMessageBox

from sdk.events import Events

from .file_open_router import FileOpenRouter


class PlatformApi:
    def __init__(self, events, commands, tasks, services, paths):
        self.events = events
        self.commands = commands
        self.tasks = tasks
        self.services = services
        self.paths = paths

        self.router = FileOpenRouter(events=events)

        self.window = None
        self._menu_reg = []      # (menu_title, label, callback, shortcut)
        self._ctx_actions = []   # (label, handler(path))
        self._log_buffer = []

        commands.register("ui.open_file", self.request_open_file)
        commands.register("ui.open_file_raw", self.request_open_file_raw)
        commands.register("ui.editor_undo", self.request_editor_undo)
        commands.register("ui.editor_redo", self.request_editor_redo)
        commands.register("ui.editor_find", self.request_editor_find)

    # -------------------------------------------------- регистрации (SDK)
    def register_menu(self, menu_title, label, callback, shortcut=None):
        self._menu_reg.append((menu_title, label, callback, shortcut))

    def register_tree_context_action(self, label, handler):
        self._ctx_actions.append((label, handler))

    @property
    def menu_registrations(self):
        return list(self._menu_reg)

    @property
    def context_actions(self):
        return list(self._ctx_actions)

    # -------------------------------------------------- привязка окна
    def bind_window(self, window):
        self.window = window
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