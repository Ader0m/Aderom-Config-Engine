# -*- coding: utf-8 -*-
"""Сборка ModuleContext из сервисов приложения."""

from __future__ import annotations

from sdk.kernel.capabilities import CapabilityRegistry, CapabilityScope
from sdk.kernel.context import ModuleContext
from sdk.kernel.registrations import RegistrationScope

from .commands import CommandApiAdapter
from .core import CoreApiAdapter
from .documents import DocumentApiAdapter
from .events import EventApiAdapter
from .items import ItemCatalogApiAdapter
from .patch import PatchApiAdapter
from .projects import ProjectApiAdapter
from .tasks import TaskApiAdapter
from .ui import UiApiAdapter


def build_module_context(
    manifest,
    *,
    sdk_version,
    events,
    commands,
    ui,
    tasks,
    services,
    paths,
    capabilities=None,
    section_graph_contributors=None,
    items=None,
):
    """Создать контекст модуля с явными адаптерами."""
    module_id = str(manifest.id or "")
    scope = RegistrationScope(module_id)

    events_api = EventApiAdapter.wrap(events)
    events_api.bind_scope(scope)
    events_api.bind_owner(module_id)

    commands_api = CommandApiAdapter.wrap(commands)
    commands_api.bind_scope(scope)
    commands_api.bind_owner(module_id)

    tasks_api = TaskApiAdapter.wrap(tasks)
    tasks_api.bind_owner(module_id)

    core_api = CoreApiAdapter(
        services,
        contributors=section_graph_contributors,
    )
    core_api.bind_owner(module_id, scope)

    ui_api = UiApiAdapter(ui)
    ui_api.bind_owner(module_id, scope)

    documents_api = DocumentApiAdapter(ui, events=events_api)
    documents_api.bind_owner(module_id, scope)

    projects_api = ProjectApiAdapter(ui)
    projects_api.bind_owner(module_id, scope)

    items_api = ItemCatalogApiAdapter(items, ui)

    if capabilities is None:
        capabilities = CapabilityScope(
            CapabilityRegistry(),
            module_id,
        )

    context = ModuleContext(
        sdk_version,
        module_id=module_id,
        services=core_api,
        ui=ui_api,
        tasks=tasks_api,
        events=events_api,
        commands=commands_api,
        paths=paths,
        capabilities=capabilities,
        documents=documents_api,
        patches=PatchApiAdapter(),
        projects=projects_api,
        items=items_api,
        registrations=scope,
    )
    ui_api.bind_context(context)
    return context
