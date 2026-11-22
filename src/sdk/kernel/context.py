# -*- coding: utf-8 -*-
"""Контекст модуля: единственная точка доступа к системе."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .capabilities import CapabilityRegistry, CapabilityScope
from .registrations import RegistrationScope
from .version import version_at_least


@dataclass
class KernelApi:
    """Версия SDK, загруженные модули и реестр возможностей."""

    sdk_version: str
    available_modules: dict = field(default_factory=dict)
    capabilities: Optional[CapabilityScope] = None


class ModuleContext:
    """Контекст одного модуля.

    Все зависимости передаются явно composition root'ом. Контекст не
    создаёт адаптеры и не импортирует внутренние слои приложения.
    """

    def __init__(
        self,
        sdk_version,
        *,
        module_id,
        services,
        ui,
        tasks,
        events,
        commands,
        paths,
        capabilities=None,
        documents=None,
        patches=None,
        projects=None,
        items=None,
        registrations=None,
        available_modules=None,
    ):
        self.sdk_version = sdk_version
        self.module_id = str(module_id or "")
        self.registrations = (
            registrations
            if registrations is not None
            else RegistrationScope(self.module_id)
        )
        if capabilities is None:
            capabilities = CapabilityScope(
                CapabilityRegistry(),
                self.module_id,
            )
        self.capabilities = capabilities
        self.kernel = KernelApi(
            sdk_version=sdk_version,
            available_modules=dict(available_modules or {}),
            capabilities=capabilities,
        )
        self.services = services
        self.ui = ui
        self.tasks = tasks
        self.events = events
        self.commands = commands
        self.paths = paths
        self.documents = documents
        self.patches = patches
        self.projects = projects
        self.items = items

    def log(self, msg):
        self.ui.log(msg)

    def has_module(self, name, min_version=""):
        """Загружен ли модуль с подходящей версией."""
        version = self.kernel.available_modules.get(str(name))
        if version is None:
            return False
        if not min_version:
            return True
        return version_at_least(version, min_version)
