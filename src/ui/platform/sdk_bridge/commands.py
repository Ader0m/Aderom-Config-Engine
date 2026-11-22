# -*- coding: utf-8 -*-
"""CommandApiAdapter: команды платформы от имени модуля."""

from __future__ import annotations

from .common import _SDK_API_MARKER


class CommandApiAdapter:
    """Реализация :class:`sdk.api.CommandApi` поверх шины команд."""

    _sdk_api_kind = "commands"

    def __init__(self, bus, scope=None):
        self._bus = bus
        self._owner = None
        self._scope = scope

    def bind_owner(self, owner):
        self._owner = str(owner or "") or None

    def bind_scope(self, scope):
        self._scope = scope

    @classmethod
    def wrap(cls, bus):
        if getattr(bus, _SDK_API_MARKER, None) == cls._sdk_api_kind:
            return bus
        return cls(bus)

    def register(self, name, handler):
        if self._owner and not str(name).startswith(self._owner + "."):
            raise ValueError(
                "Команда модуля %s должна начинаться с '%s.': %s"
                % (self._owner, self._owner, name)
            )
        result = self._bus.register(name, handler, owner=self._owner)
        if self._scope is not None:
            command_name = str(name)
            owner = self._owner
            self._scope.track_callable(
                lambda: self._bus.unregister(command_name, owner=owner)
            )
        return result

    def unregister(self, name):
        return self._bus.unregister(name, owner=self._owner)

    def has(self, name):
        return self._bus.has(name)

    def execute(self, name, *args, **kwargs):
        return self._bus.execute(name, *args, **kwargs)
