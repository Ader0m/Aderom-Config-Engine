# -*- coding: utf-8 -*-
"""Реестр возможностей (capabilities) SDK.

Capability — типизированная возможность, которую предоставляет модуль.
Потребитель не знает имя провайдера и не импортирует его код.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Dict, Optional

from .version import version_at_least


class CapabilityError(RuntimeError):
    """Базовая ошибка capability."""


class MissingCapability(CapabilityError):
    """Возможность не предоставлена."""


class CapabilityVersionError(CapabilityError):
    """Версия возможности ниже требуемой."""


class CapabilityUnavailable(CapabilityError):
    """Возможность объявлена, но объект ещё не предоставлен."""


class CapabilityConflictError(CapabilityError):
    """Конфликт владельцев одной возможности."""


@dataclass
class Capability:
    """Запись реестра: имя, версия, владелец и объект."""

    name: str
    version: str = "1.0.0"
    owner: str = ""
    obj: object = None
    declared: bool = True


class CapabilityRegistry:
    """Общий реестр возможностей приложения."""

    def __init__(self):
        self._items: Dict[str, Capability] = {}
        self._lock = threading.RLock()

    def declare(self, name, version="1.0.0", owner="") -> Capability:
        """Объявить возможность по манифесту модуля.

        Одна capability принадлежит одному владельцу. Попытка объявить её
        от другого имени — конфликт, а не выбор «первого победителя».
        """
        key = str(name)
        wanted_owner = str(owner or "")
        with self._lock:
            current = self._items.get(key)
            if current is None:
                current = Capability(
                    name=key,
                    version=str(version or "1.0.0"),
                    owner=wanted_owner,
                )
                self._items[key] = current
                return current
            if current.owner and wanted_owner and current.owner != wanted_owner:
                raise CapabilityConflictError(
                    "Возможность %s уже принадлежит модулю %s, "
                    "модуль %s не может её объявить"
                    % (key, current.owner, wanted_owner)
                )
            if not current.owner and wanted_owner:
                current.owner = wanted_owner
            if version and version_at_least(version, current.version):
                current.version = str(version)
            current.declared = True
            return current

    def provide(self, name, version, obj, owner="") -> Capability:
        """Предоставить реализацию возможности.

        Заменять реализацию другого владельца запрещено.
        """
        key = str(name)
        wanted_owner = str(owner or "")
        with self._lock:
            current = self._items.get(key)
            if current is None:
                current = Capability(
                    name=key,
                    version=str(version or "1.0.0"),
                    owner=wanted_owner,
                    obj=obj,
                )
                self._items[key] = current
                return current
            if current.owner and wanted_owner and current.owner != wanted_owner:
                raise CapabilityConflictError(
                    "Возможность %s уже принадлежит модулю %s; "
                    "модуль %s не может её заменить"
                    % (key, current.owner, wanted_owner)
                )
            if current.owner and not wanted_owner:
                raise CapabilityConflictError(
                    "Возможность %s уже принадлежит модулю %s; "
                    "анонимный провайдер не может её заменить"
                    % (key, current.owner)
                )
            if not current.owner and wanted_owner:
                current.owner = wanted_owner
            current.version = str(version or "1.0.0")
            current.obj = obj
            current.declared = True
            return current

    def discard(self, name) -> bool:
        """Удалить запись возможности из реестра."""
        with self._lock:
            return self._items.pop(str(name), None) is not None

    def remove_owner(self, owner) -> int:
        """Удалить все возможности указанного владельца."""
        wanted = str(owner or "")
        removed = 0
        with self._lock:
            for key in list(self._items):
                if self._items[key].owner == wanted:
                    del self._items[key]
                    removed += 1
        return removed

    def get(self, name) -> Optional[Capability]:
        with self._lock:
            return self._items.get(str(name))

    def has(self, name, min_version="") -> bool:
        item = self.get(name)
        if item is None:
            return False
        if min_version and not version_at_least(item.version, min_version):
            return False
        return True

    def version(self, name) -> Optional[str]:
        item = self.get(name)
        return None if item is None else item.version

    def provider(self, name) -> Optional[str]:
        item = self.get(name)
        return None if item is None else item.owner

    def require(self, name, min_version="0.0.0"):
        item = self.get(name)
        if item is None:
            raise MissingCapability("Возможность не предоставлена: %s" % name)
        if min_version and not version_at_least(item.version, min_version):
            raise CapabilityVersionError(
                "Возможность %s: нужна версия >= %s, есть %s"
                % (name, min_version, item.version)
            )
        if item.obj is None:
            raise CapabilityUnavailable(
                "Возможность %s объявлена, но ещё не предоставлена" % name
            )
        return item.obj

    def optional(self, name, min_version="0.0.0"):
        item = self.get(name)
        if item is None or item.obj is None:
            return None
        if min_version and not version_at_least(item.version, min_version):
            return None
        return item.obj

    def snapshot(self) -> dict:
        with self._lock:
            return {
                key: {
                    "version": item.version,
                    "owner": item.owner,
                    "available": item.obj is not None,
                }
                for key, item in self._items.items()
            }


class CapabilityScope:
    """Вид реестра от имени конкретного модуля."""

    def __init__(self, registry: CapabilityRegistry, owner: str):
        self._registry = registry
        self._owner = str(owner or "")

    @property
    def owner(self) -> str:
        return self._owner

    def provide(self, name, version, obj) -> Capability:
        return self._registry.provide(name, version, obj, owner=self._owner)

    def require(self, name, min_version="0.0.0"):
        return self._registry.require(name, min_version=min_version)

    def optional(self, name, min_version="0.0.0"):
        return self._registry.optional(name, min_version=min_version)

    def has(self, name, min_version="") -> bool:
        return self._registry.has(name, min_version=min_version)

    def version(self, name) -> Optional[str]:
        return self._registry.version(name)

    def provider(self, name) -> Optional[str]:
        return self._registry.provider(name)

    def snapshot(self) -> dict:
        return self._registry.snapshot()
