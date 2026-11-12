# -*- coding: utf-8 -*-
"""Базовый контракт модуля и контекст модуля."""

from dataclasses import dataclass


@dataclass
class ModuleManifest:
    name: str
    version: str = "0.0.0"
    sdk_min: str = "0.1.0"
    description: str = ""
    author: str = ""
    # Имена обязательных модулей, которые должны быть загружены раньше.
    requires: tuple = ()


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

    def __init__(self, sdk_version, events, commands, ui, tasks, services, paths):
        from .adapters import (
            CommandApiAdapter,
            CoreApiAdapter,
            EventApiAdapter,
            TaskApiAdapter,
            UiApiAdapter,
        )

        self.sdk_version = sdk_version
        self.events = EventApiAdapter.wrap(events)
        self.commands = CommandApiAdapter.wrap(commands)
        self.ui = UiApiAdapter.wrap(ui)
        self.tasks = TaskApiAdapter.wrap(tasks)
        self.services = CoreApiAdapter.wrap(services)
        self.paths = paths

    def log(self, msg):
        self.ui.log(msg)

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