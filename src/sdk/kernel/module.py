# -*- coding: utf-8 -*-
"""Базовый контракт модуля и манифест."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModuleRequirement:
    """Зависимость модуля: имя и минимальная версия."""

    name: str
    min_version: str = ""


def normalize_requirement(value) -> ModuleRequirement:
    """Привести строку/пару к :class:`ModuleRequirement`."""
    if isinstance(value, ModuleRequirement):
        return value
    if isinstance(value, str):
        return ModuleRequirement(name=value)
    if isinstance(value, (tuple, list)) and value:
        name = str(value[0])
        min_version = str(value[1]) if len(value) > 1 else ""
        return ModuleRequirement(name=name, min_version=min_version)
    raise TypeError("Некорректная зависимость модуля: %r" % (value,))


def normalize_requirements(values) -> tuple:
    if isinstance(values, (str, ModuleRequirement)):
        values = (values,)
    return tuple(normalize_requirement(value) for value in (values or ()))


@dataclass
class ModuleManifest:
    name: str
    version: str = "0.0.0"
    sdk_min: str = "0.1.0"
    sdk_max: str = ""
    description: str = ""
    author: str = ""
    # Имена обязательных модулей, которые должны быть загружены раньше.
    # Элемент — строка или ModuleRequirement.
    requires: tuple = ()
    # Необязательные зависимости: отсутствие модуля не блокирует загрузку.
    optional_requires: tuple = ()
    # Возможности, которые предоставляет модуль.
    provides: tuple = ()
    # Возможности, которые модулю нужны.
    needs: tuple = ()
    optional_needs: tuple = ()


class ModuleBase:
    """
    Базовый класс модуля.

    Жизненный цикл:
        init -> register -> activate -> ... -> deactivate
    """

    manifest: ModuleManifest = None

    def init(self, ctx):
        self.ctx = ctx

    def register(self, ctx):
        pass

    def activate(self, ctx):
        pass

    def deactivate(self, ctx):
        pass
