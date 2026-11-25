# -*- coding: utf-8 -*-
"""Публичный ``ctx.capabilities`` для модуля (ADR-32).

Класс связывает capability-запросы модуля с платформенным реестром и
проверяет, что фактический ``provide`` точно совпадает с ``provides``
манифеста. ``provide`` разрешён только во время ``register``.
"""

from __future__ import annotations

from sdk import (
    CapabilityError,
    CapabilityUnavailable,
)


class ModuleCapabilities:
    """Реализация :class:`sdk.CapabilitiesApi` для одного модуля."""

    def __init__(self, registry, owner, declared, log=None):
        self._registry = registry
        self._owner = str(owner or "")
        self._declared = {
            str(item.name): str(item.version)
            for item in (declared or ())
        }
        self._provided = {}
        self._registering = False
        self._closed = False
        self._log = log or (lambda message: None)

    # -------------------------------------------------- платформа
    def begin_register(self):
        if self._closed:
            raise CapabilityUnavailable(
                "Модуль %s уже выгружен" % self._owner
            )
        self._registering = True

    def end_register(self):
        self._registering = False
        missing = sorted(set(self._declared) - set(self._provided))
        if missing:
            raise CapabilityError(
                "Модуль %s не предоставил объявленные capability: %s"
                % (self._owner, ", ".join(missing)),
                kind="missing_provide",
            )

    def close(self):
        self._registering = False
        self._closed = True

    def snapshot(self):
        return self._registry.snapshot()

    # -------------------------------------------------- CapabilitiesApi
    def provide(self, name, version, obj):
        name = str(name)
        version = str(version)
        if self._closed:
            raise CapabilityUnavailable(
                "Модуль %s уже выгружен" % self._owner
            )
        if not self._registering:
            raise CapabilityError(
                "provide(%s) разрешён только в register" % name,
                kind="provide_outside_register",
            )
        expected = self._declared.get(name)
        if expected is None:
            raise CapabilityError(
                "Модуль %s не объявлял capability %s"
                % (self._owner, name),
                kind="undeclared_capability",
            )
        if version != expected:
            raise CapabilityError(
                "Модуль %s: версия %s для %s не совпадает с манифестом %s"
                % (self._owner, version, name, expected),
                kind="capability_version_mismatch",
            )
        existing = self._provided.get(name)
        if existing is not None:
            return self._registry.get(name)
        self._provided[name] = version
        return self._registry.provide(
            name,
            version,
            obj,
            owner=self._owner,
        )

    def require(self, name, min_version="1.0.0"):
        return self._registry.require(name, min_version=min_version)

    def optional(self, name, min_version="1.0.0"):
        return self._registry.optional(name, min_version=min_version)

    def has(self, name, min_version="") -> bool:
        return self._registry.has(name, min_version=min_version)

    def version(self, name):
        return self._registry.version(name)

    def provider(self, name):
        return self._registry.provider(name)
