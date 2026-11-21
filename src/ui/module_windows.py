# -*- coding: utf-8 -*-
"""Хостинг окон, которые создают модули.

Платформа не знает, что именно показывает модуль. Она отвечает только за:
- показ, подъём и активацию;
- закрытие окон модуля при необходимости.

Окна модулей намеренно не делаются дочерними (owned) для главного окна:
на Windows owned-окно всегда рисуется поверх владельца, из-за чего клик
по главному окну не мог поднять его над открытым окном модуля. Поэтому
каждое окно модуля — самостоятельное top-level окно, а его жизненным
циклом управляет этот менеджер.
"""

from PyQt5.QtWidgets import QWidget


class ModuleWindowManager:
    """Хранит и показывает окна, принадлежащие модулям."""

    def __init__(self):
        self._main_window = None
        self._windows = {}
        self._owners = {}

    def bind_main_window(self, window):
        self._main_window = window

    def show(self, key, window, owner=None):
        if not isinstance(window, QWidget):
            raise TypeError("Окно модуля должно быть QWidget")

        if key:
            previous = self._windows.get(key)
            if previous is not None and previous is not window:
                previous.close()
            self._windows[key] = window
            self._owners[key] = str(owner or "")

        window.show()
        window.raise_()
        window.activateWindow()

    def owner_of(self, key):
        return self._owners.get(key, "")

    def close(self, key):
        window = self._windows.pop(key, None)
        self._owners.pop(key, None)
        if window is not None:
            window.close()

    def close_owner(self, owner):
        wanted = str(owner or "")
        keys = [
            key
            for key, window_owner in self._owners.items()
            if window_owner == wanted
        ]
        for key in keys:
            self.close(key)
        return len(keys)

    def close_all(self):
        for window in list(self._windows.values()):
            window.close()
        self._windows.clear()
        self._owners.clear()
