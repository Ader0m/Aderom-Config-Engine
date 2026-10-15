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
    Всё разрешённое доступно только через этот контекст.
    """

    def __init__(self, sdk_version, events, commands, ui, tasks, services, paths):
        self.sdk_version = sdk_version
        self.events = events
        self.commands = commands
        self.ui = ui
        self.tasks = tasks
        self.services = services
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