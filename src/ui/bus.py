# -*- coding: utf-8 -*-
"""Шина событий и шина команд."""

import logging
import traceback

log = logging.getLogger(__name__)


class EventBus:
    """Событие — это факт, который уже произошел."""

    def __init__(self):
        self._subs = {}

    def subscribe(self, name, fn):
        self._subs.setdefault(name, []).append(fn)

    def publish(self, name, payload=None):
        for fn in list(self._subs.get(name, [])):
            try:
                fn(payload)
            except Exception:
                log.error(
                    "Ошибка подписчика события %s:\n%s",
                    name,
                    traceback.format_exc(),
                )


class CommandError(Exception):
    pass


class CommandBus:
    """Команда — это намерение что-то сделать."""

    def __init__(self):
        self._handlers = {}

    def register(self, name, handler):
        self._handlers[name] = handler

    def has(self, name):
        return name in self._handlers

    def execute(self, name, *args, **kwargs):
        h = self._handlers.get(name)
        if h is None:
            raise CommandError("Команда не зарегистрирована: %s" % name)
        return h(*args, **kwargs)