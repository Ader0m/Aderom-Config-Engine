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

from PyQt5.QtCore import QEvent, QObject
from PyQt5.QtWidgets import QWidget

from sdk.contracts.events import PLATFORM_OWNER


def _is_platform_owner(owner):
    """``None``, пустой owner и ``<platform>`` — доверенная платформа."""
    return owner is None or str(owner) in ("", PLATFORM_OWNER)


class _WindowCloseFilter(QObject):
    """Сохраняет геометрию окна при закрытии пользователем через X."""

    def __init__(self, manager, key, window):
        super().__init__(window)
        self._manager = manager
        self._key = key
        self._window = window

    def eventFilter(self, _obj, event):
        if event.type() == QEvent.Close:
            self._manager._save_closed_window(
                self._key,
                self._window,
            )
        return False


class ModuleWindowManager:
    """Хранит и показывает окна, принадлежащие модулям.

    Платформенный владелец (``None``/пустой/``<platform>``) доверенный:
    может заменить или закрыть любое окно. Модуль управляет только окнами
    со своим owner.
    """

    def __init__(self, layout=None):
        self._main_window = None
        self._windows = {}
        self._owners = {}
        self._state_keys = {}
        self._layout = layout

    def bind_main_window(self, window):
        self._main_window = window

    def show(self, key, window, owner=None, state_key=""):
        if not isinstance(window, QWidget):
            raise TypeError("Окно модуля должно быть QWidget")

        owner_value = str(owner or "")
        platform_owner = _is_platform_owner(owner)
        restore_geometry = True
        if key:
            previous = self._windows.get(key)
            if previous is not None and previous is not window:
                previous_owner = self._owners.get(key, "")
                if not platform_owner:
                    if previous_owner and previous_owner != owner_value:
                        return False
                    if not previous_owner and owner_value:
                        return False
                if self._layout is not None:
                    self._layout.save_module_window(
                        key,
                        previous_owner,
                        previous,
                        state_key=self._state_keys.get(key, ""),
                    )
                previous.close()
            elif previous is window:
                restore_geometry = False
                previous_owner = self._owners.get(key, "")
                if not platform_owner:
                    if previous_owner and previous_owner != owner_value:
                        return False
                    if not previous_owner and owner_value:
                        return False
            self._windows[key] = window
            self._owners[key] = owner_value
            self._state_keys[key] = str(state_key or "")
            self._install_close_filter(key, window)

        if (
            restore_geometry
            and key
            and self._layout is not None
        ):
            self._layout.restore_module_window(key, window)

        window.show()
        window.raise_()
        window.activateWindow()
        return True

    def focus(self, key):
        """Поднять уже показанное окно по ключу. ``True`` при успехе."""
        window = self._windows.get(str(key or ""))
        if window is None:
            return False
        window.show()
        window.raise_()
        window.activateWindow()
        return True

    def owner_of(self, key):
        return self._owners.get(key, "")

    def _install_close_filter(self, key, window):
        if getattr(window, "_ace_close_filter", None) is not None:
            return
        window._ace_close_filter = _WindowCloseFilter(
            self,
            key,
            window,
        )
        window.installEventFilter(window._ace_close_filter)

    def _save_window_layout(self, key, window):
        if self._layout is None or window is None:
            return
        self._layout.save_module_window(
            key,
            self._owners.get(key, ""),
            window,
            state_key=self._state_keys.get(key, ""),
        )

    def _save_closed_window(self, key, window):
        if self._windows.get(key) is not window:
            return
        self._save_window_layout(key, window)
        self._windows.pop(key, None)
        self._owners.pop(key, None)
        self._state_keys.pop(key, None)

    def close(self, key, owner=None):
        stored_owner = self._owners.get(key, "")
        if not _is_platform_owner(owner) and stored_owner != str(owner):
            return False
        window = self._windows.pop(key, None)
        self._owners.pop(key, None)
        state_key = self._state_keys.pop(key, "")
        if window is not None:
            if self._layout is not None:
                self._layout.save_module_window(
                    key,
                    stored_owner,
                    window,
                    state_key=state_key,
                )
            window.close()
            return True
        return False

    def close_owner(self, owner):
        wanted = str(owner or "")
        if not wanted:
            return 0
        keys = [
            key
            for key, window_owner in self._owners.items()
            if window_owner == wanted
        ]
        for key in keys:
            self.close(key)
        return len(keys)

    def close_all(self):
        for key in list(self._windows):
            self.close(key)
