# -*- coding: utf-8 -*-
"""Горячие клавиши и мышиные привязки, зарегистрированные модулями."""

from PyQt5.QtCore import QEvent, QObject, Qt
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import QApplication, QShortcut

from sdk.contracts.events import Events
from sdk.kernel.registrations import RegistrationHandle, remove_identity


_MODIFIERS = {
    "ctrl": int(Qt.ControlModifier),
    "control": int(Qt.ControlModifier),
    "shift": int(Qt.ShiftModifier),
    "alt": int(Qt.AltModifier),
    "meta": int(Qt.MetaModifier),
    "win": int(Qt.MetaModifier),
}

_BUTTONS = {
    "left": Qt.LeftButton,
    "right": Qt.RightButton,
    "middle": Qt.MiddleButton,
}

_MOUSE_EVENTS = {
    "press": QEvent.MouseButtonPress,
    "click": QEvent.MouseButtonPress,
    "release": QEvent.MouseButtonRelease,
    "double": QEvent.MouseButtonDblClick,
    "dblclick": QEvent.MouseButtonDblClick,
    "move": QEvent.MouseMove,
}


def normalize_modifiers(value) -> int:
    """Привести ``"Ctrl+Shift"`` / список / Qt-флаги к битовой маске."""
    if value is None:
        return 0

    if isinstance(value, str):
        result = 0
        for part in value.replace("+", " ").split():
            result |= _MODIFIERS.get(part.lower(), 0)
        return result

    if isinstance(value, (list, tuple, set)):
        result = 0
        for item in value:
            result |= normalize_modifiers(item)
        return result

    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def normalize_button(value):
    if isinstance(value, str):
        return _BUTTONS.get(value.lower(), Qt.LeftButton)
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(Qt.LeftButton)


def normalize_mouse_event(value):
    if isinstance(value, str):
        return _MOUSE_EVENTS.get(value.lower(), QEvent.MouseButtonPress)
    return value


class ShortcutManager:
    """Создаёт QShortcut после появления главного окна."""

    def __init__(self, trigger):
        self._trigger = trigger
        self._window = None
        self._registrations = []
        self._installed = set()

    def register(self, key, callback, scope="global", action=None, owner=None):
        registration = {
            "key": key,
            "callback": callback,
            "scope": scope or "global",
            "action": action,
            "object": None,
            "owner": str(owner or ""),
        }
        self._registrations.append(registration)
        if self._window is not None:
            self._install(registration)
        return RegistrationHandle(
            lambda: self._unregister(registration),
            registration["owner"],
        )

    def _unregister(self, registration):
        shortcut = registration.get("object")
        if shortcut is not None:
            try:
                shortcut.setEnabled(False)
                shortcut.deleteLater()
            except RuntimeError:
                pass
            self._installed.discard(id(registration))
            registration["object"] = None
        return remove_identity(self._registrations, registration)

    def attach(self, window):
        self._window = window
        for registration in self._registrations:
            self._install(registration)

    def _install(self, registration):
        if id(registration) in self._installed:
            return

        target = self._target(registration["scope"])
        if target is None:
            return

        shortcut = QShortcut(QKeySequence(registration["key"]), target)
        shortcut.setContext(self._context(registration["scope"]))
        shortcut.activated.connect(
            lambda r=registration: self._trigger(
                r["action"],
                r["callback"],
                {"key": r["key"], "scope": r["scope"]},
                Events.UI_SHORTCUT,
            )
        )
        registration["object"] = shortcut
        self._installed.add(id(registration))

    def _target(self, scope):
        if self._window is None:
            return None
        if scope == "editor":
            return getattr(self._window, "editor", None)
        if scope == "tree":
            return getattr(self._window, "file_tree", None)
        return self._window

    @staticmethod
    def _context(scope):
        if scope in ("editor", "tree"):
            return Qt.WidgetWithChildrenShortcut
        return Qt.WindowShortcut


class MouseBindingManager:
    """Ставит event-filter на окно/дерево/редактор для привязок модулей."""

    def __init__(self, trigger):
        self._trigger = trigger
        self._window = None
        self._registrations = []
        self._filters = {}

    def register(
        self,
        button,
        modifiers,
        callback,
        scope="global",
        event="press",
        action=None,
        owner=None,
    ):
        registration = {
            "button": normalize_button(button),
            "modifiers": normalize_modifiers(modifiers),
            "callback": callback,
            "scope": scope or "global",
            "event_type": normalize_mouse_event(event),
            "action": action,
            "owner": str(owner or ""),
        }
        self._registrations.append(registration)
        if self._window is not None:
            self._ensure_filter(registration["scope"])
        return RegistrationHandle(
            lambda: self._unregister(registration),
            registration["owner"],
        )

    def _unregister(self, registration):
        if not remove_identity(self._registrations, registration):
            return False

        scope = registration["scope"]
        if any(item["scope"] == scope for item in self._registrations):
            return True

        event_filter = self._filters.pop(scope, None)
        if event_filter is not None:
            target = self._target(scope)
            if target is not None:
                try:
                    target.removeEventFilter(event_filter)
                except RuntimeError:
                    pass
            try:
                event_filter.deleteLater()
            except RuntimeError:
                pass
        return True

    def attach(self, window):
        self._window = window
        for registration in self._registrations:
            self._ensure_filter(registration["scope"])

    def handle(self, scope, event):
        for registration in self._registrations:
            if registration["scope"] != scope:
                continue
            if event.type() != registration["event_type"]:
                continue
            if not self._button_matches(registration, event):
                continue
            if int(event.modifiers()) != registration["modifiers"]:
                continue

            payload = {
                "scope": scope,
                "button": registration["button"],
                "modifiers": registration["modifiers"],
                "event": event,
            }
            return self._trigger(
                registration["action"],
                lambda e=event: registration["callback"](e),
                payload,
                Events.UI_MOUSE,
            )
        return False

    @staticmethod
    def _button_matches(registration, event):
        if event.type() == QEvent.MouseMove:
            # У MouseMove ``button()`` всегда NoButton, нужна маска ``buttons()``.
            return bool(int(event.buttons()) & registration["button"])
        return int(event.button()) == registration["button"]

    def _ensure_filter(self, scope):
        target = self._target(scope)
        if target is None or scope in self._filters:
            return

        mouse_filter = _MouseFilter(self, scope)
        target.installEventFilter(mouse_filter)
        self._filters[scope] = mouse_filter

    def _target(self, scope):
        if self._window is None:
            return None
        if scope == "editor":
            return getattr(self._window, "editor", None)
        if scope == "tree":
            return getattr(self._window, "file_tree", None)
        return QApplication.instance() or self._window


class _MouseFilter(QObject):
    def __init__(self, manager, scope):
        super().__init__()
        self._manager = manager
        self._scope = scope

    def eventFilter(self, obj, event):
        if self._manager.handle(self._scope, event):
            return True
        return super().eventFilter(obj, event)
