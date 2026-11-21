# -*- coding: utf-8 -*-
"""Базовый контракт модуля и контекст модуля."""

from dataclasses import dataclass

from .version import version_at_least


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


class ModuleContext:
    """
    Единственная точка доступа модуля к системе.

    Модуль не импортирует core и не импортирует ui.
    Всё разрешённое доступно только через этот контекст:

    - ``ctx.services`` — :class:`sdk.api.CoreApi`;
    - ``ctx.ui`` — :class:`sdk.api.UiApi`;
    - ``ctx.tasks`` — :class:`sdk.api.TaskApi`;
    - ``ctx.events`` / ``ctx.commands`` — шины платформы.

    Композиционный корень передаёт сюда реальные сервисы, а контекст
    сам оборачивает их в SDK-адаптеры.
    """

    def __init__(
        self,
        sdk_version,
        events,
        commands,
        ui,
        tasks,
        services,
        paths,
        module_name=None,
    ):
        from .adapters import (
            CommandApiAdapter,
            CoreApiAdapter,
            EventApiAdapter,
            TaskApiAdapter,
            UiApiAdapter,
        )
        from .registrations import RegistrationScope

        self.sdk_version = sdk_version
        self.module_name = str(module_name or "")
        self.available_modules = {}
        self.capabilities = {}
        self.registrations = RegistrationScope(module_name)
        self.events = EventApiAdapter.wrap(events)
        self.commands = CommandApiAdapter.wrap(commands)
        self.ui = UiApiAdapter.wrap(ui)
        self.tasks = TaskApiAdapter.wrap(tasks)
        self.services = CoreApiAdapter.wrap(services)
        self.paths = paths

        events_binder = getattr(self.events, "bind_scope", None)
        if callable(events_binder):
            events_binder(self.registrations)
        events_owner = getattr(self.events, "bind_owner", None)
        if callable(events_owner):
            events_owner(module_name)
        commands_binder = getattr(self.commands, "bind_scope", None)
        if callable(commands_binder):
            commands_binder(self.registrations)
        for target, binder_name, args in (
            (self.commands, "bind_owner", (module_name,)),
            (self.tasks, "bind_owner", (module_name,)),
            (self.services, "bind_owner", (module_name, self.registrations)),
        ):
            binder = getattr(target, binder_name, None)
            if callable(binder):
                binder(*args)

        binder = getattr(self.ui, "bind_context", None)
        if callable(binder):
            binder(self)

    def log(self, msg):
        self.ui.log(msg)

    def has_module(self, name, min_version=""):
        """Загружен ли модуль с подходящей версией."""
        version = self.available_modules.get(str(name))
        if version is None:
            return False
        if not min_version:
            return True
        return version_at_least(version, min_version)

    def has_capability(self, name):
        """Предоставлена ли возможность другим модулем."""
        return str(name) in self.capabilities

    def capability_provider(self, name):
        return self.capabilities.get(str(name))


class FileOpenHandler:
    """
    Контракт обработчика открытия файла.
    Регистрируется через ctx.ui.register_open_handler(...).
    Чем выше priority, тем раньше обработчик претендует на файл.
    """

    priority = 100

    def can_open(self, path):
        return False

    def open(self, path, api):
        raise NotImplementedError


class FileReader:
    """Читатель файла для проекта.

    Регистрируется через ``ctx.ui.register_file_reader(...)``. Проект
    выбирает первый подходящий читатель по ``priority`` (больше — раньше)
    и ``can_read``. Читатель возвращает :class:`sdk.api.FileView` —
    итоговый вид файла для предпросмотра, поиска или других сценариев.

    ``read`` вызывается в фоновом потоке, поэтому не должен трогать
    Qt-виджеты и изменяемое состояние проекта.
    """

    priority = 100

    def can_read(self, path, data=None):
        return False

    def read(self, path, data=None):
        raise NotImplementedError