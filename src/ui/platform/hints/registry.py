# -*- coding: utf-8 -*-
"""Реестр провайдеров подсказок."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class ProviderSubscription:
    def __init__(self, registry, entry):
        self._registry = registry
        self._entry = entry
        self._active = True

    @property
    def active(self):
        return self._active

    def unsubscribe(self):
        if self._active:
            self._registry._remove(self._entry)
            self._active = False


class _Entry:
    __slots__ = ("provider", "order", "active")

    def __init__(self, provider, order):
        self.provider = provider
        self.order = order
        self.active = True


class HintRegistry:
    def __init__(self):
        self._entries = []
        self._order = 0

    def register(self, provider):
        provider_id = str(getattr(provider, "id", "") or "")
        if not provider_id:
            raise ValueError("Провайдер подсказок должен иметь id")
        for entry in self._entries:
            if (
                entry.active
                and str(getattr(entry.provider, "id", "")) == provider_id
            ):
                raise ValueError(
                    "Провайдер подсказок уже зарегистрирован: %s"
                    % provider_id
                )
        self._order += 1
        entry = _Entry(provider, self._order)
        self._entries.append(entry)
        return ProviderSubscription(self, entry)

    def _remove(self, entry):
        entry.active = False
        self._entries = [
            item for item in self._entries if item is not entry
        ]

    def unregister(self, provider_id):
        for entry in list(self._entries):
            if (
                entry.active
                and str(getattr(entry.provider, "id", "")) == provider_id
            ):
                self._remove(entry)
                return True
        return False

    def clear(self):
        for entry in self._entries:
            entry.active = False
        self._entries.clear()

    def snapshot(self):
        entries = [entry for entry in self._entries if entry.active]
        entries.sort(
            key=lambda entry: (
                -int(getattr(entry.provider, "priority", 0) or 0),
                entry.order,
            )
        )
        return tuple(entry.provider for entry in entries)

    def __len__(self):
        return len(self._entries)

    def notify_catalogs_changed(self):
        for provider in self.snapshot():
            callback = getattr(provider, "on_catalogs_changed", None)
            if not callable(callback):
                continue
            try:
                callback()
            except Exception:
                log.exception(
                    "Ошибка on_catalogs_changed провайдера %s",
                    getattr(provider, "id", "?"),
                )
