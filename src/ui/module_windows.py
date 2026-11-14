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
