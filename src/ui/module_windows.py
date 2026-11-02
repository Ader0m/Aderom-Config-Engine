# -*- coding: utf-8 -*-
"""Хостинг окон, которые создают модули.

Платформа не знает, что именно показывает модуль. Она отвечает только за:
- родительское окно;
- показ, подъём и активацию;
- закрытие окон модуля при необходимости.
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QWidget


class ModuleWindowManager:
    """Хранит и показывает окна, принадлежащие модулям."""

    def __init__(self):
        self._main_window = None
        self._windows = {}

    def bind_main_window(self, window):
        self._main_window = window

    def show(self, key, window):
        if not isinstance(window, QWidget):
            raise TypeError("Окно модуля должно быть QWidget")

        if key:
            previous = self._windows.get(key)
            if previous is not None and previous is not window:
                previous.close()
            self._windows[key] = window

        if self._main_window is not None and window.parent() is None:
            window.setParent(self._main_window, window.windowFlags() | Qt.Window)

        window.show()
        window.raise_()
        window.activateWindow()

    def close(self, key):
        window = self._windows.pop(key, None)
        if window is not None:
            window.close()

    def close_all(self):
        for window in list(self._windows.values()):
            window.close()
        self._windows.clear()
