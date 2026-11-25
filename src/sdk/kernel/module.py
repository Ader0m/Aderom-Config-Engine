# -*- coding: utf-8 -*-
"""Базовый контракт модуля и in-memory манифест.

Манифест модуля хранится только в ``module.json`` (ADR-31). Класс
модуля больше не объявляет ``manifest``: загрузчик читает и валидирует
файл до импорта кода. :class:`ModuleManifest` — результат этого чтения,
а не второй способ объявления манифеста.
"""

from dataclasses import dataclass

# Версия схемы ``module.json``. Другие версии не поддерживаются.
MODULE_MANIFEST_VERSION = 1


@dataclass(frozen=True)
class ModuleRequirement:
    """Зависимость модуля: id модуля и минимальная версия."""

    id: str
    min_version: str = ""


@dataclass(frozen=True)
class CapabilityDeclaration:
    """Capability, которую модуль предоставляет."""

    name: str
    version: str = "1.0.0"


@dataclass(frozen=True)
class CapabilityRequirement:
    """Capability, которая нужна модулю."""

    name: str
    min_version: str = "1.0.0"


def normalize_requirement(value) -> ModuleRequirement:
    """Привести строку/пару к :class:`ModuleRequirement`."""
    if isinstance(value, ModuleRequirement):
        return value
    if isinstance(value, str):
        return ModuleRequirement(id=value)
    if isinstance(value, (tuple, list)) and value:
        module_id = str(value[0])
        min_version = str(value[1]) if len(value) > 1 else ""
        return ModuleRequirement(id=module_id, min_version=min_version)
    raise TypeError("Некорректная зависимость модуля: %r" % (value,))


def normalize_requirements(values) -> tuple:
    if isinstance(values, (str, ModuleRequirement)):
        values = (values,)
    return tuple(normalize_requirement(value) for value in (values or ()))


@dataclass(frozen=True)
class ModuleManifest:
    """Проверенное содержимое ``module.json``.

    ``id`` — единственный identity и namespace модуля. ``name`` — только
    отображаемое имя.
    """

    id: str
    name: str
    version: str = "0.0.0"
    manifest_version: int = MODULE_MANIFEST_VERSION
    entrypoint: str = "module:MODULE"
    sdk_min: str = "0.1.0"
    sdk_max: str = ""
    description: str = ""
    author: str = ""
    # Обязательные зависимости по id модуля.
    requires: tuple = ()
    # Необязательные зависимости: отсутствие модуля не блокирует загрузку.
    optional_requires: tuple = ()
    # Capability, которые модуль предоставляет.
    provides: tuple = ()
    # Capability, которые модулю нужны.
    needs: tuple = ()
    optional_needs: tuple = ()
    trust: str = "trusted"
    enabled_by_default: bool = True
    platforms: tuple = ("windows",)


class ModuleBase:
    """
    Базовый класс модуля.

    Жизненный цикл:
        init -> register -> activate -> ... -> deactivate
    """

    def init(self, ctx):
        self.ctx = ctx

    def register(self, ctx):
        pass

    def activate(self, ctx):
        pass

    def deactivate(self, ctx):
        pass
