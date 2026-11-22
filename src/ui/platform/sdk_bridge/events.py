# -*- coding: utf-8 -*-
"""EventApiAdapter: события платформы от имени модуля."""

from __future__ import annotations

from sdk.contracts.events import PLATFORM_OWNER, namespace_of

from .common import _SDK_API_MARKER, log


class EventApiAdapter:
    """Реализация :class:`sdk.api.EventApi` поверх шины событий."""

    _sdk_api_kind = "events"

    def __init__(self, bus, scope=None):
        self._bus = bus
        self._scope = scope
        self._owner = PLATFORM_OWNER

    def bind_scope(self, scope):
        """Все подписки модуля попадут в его RegistrationScope."""
        self._scope = scope

    def bind_owner(self, owner):
        """Имя модуля-владельца для снятия только своих подписок."""
        self._owner = str(owner or "")

    @classmethod
    def wrap(cls, bus):
        if getattr(bus, _SDK_API_MARKER, None) == cls._sdk_api_kind:
            return bus
        return cls(bus)

    def subscribe(self, name, handler, priority=0):
        subscription = self._bus.subscribe(
            name,
            handler,
            priority=priority,
            owner=self._owner,
        )
        if self._scope is not None:
            self._scope.track(subscription)
        return subscription

    def unsubscribe(self, name, handler=None):
        return self._bus.unsubscribe(
            name,
            handler,
            owner=self._owner,
        )

    def publish(self, name, payload=None):
        prefix = namespace_of(name)
        if (
            self._owner
            and self._owner != PLATFORM_OWNER
            and prefix not in ("", "app", "ui", self._owner)
        ):
            log.warning(
                "Модуль %s публикует событие чужого namespace: %s",
                self._owner,
                name,
            )
        return self._bus.publish(name, payload)

    def dispatch(self, name, payload=None):
        return self._bus.dispatch(name, payload)

    def has(self, name):
        return self._bus.has(name)
