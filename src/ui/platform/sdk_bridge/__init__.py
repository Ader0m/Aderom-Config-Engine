# -*- coding: utf-8 -*-
"""Transitional bridge между SDK-контрактами и core/ui.

Адаптеры живут вне ``sdk``: SDK не импортирует внутренние слои.
"""

from .commands import CommandApiAdapter
from .context import build_module_context
from .core import CoreApiAdapter
from .documents import DocumentApiAdapter
from .events import EventApiAdapter
from .items import ItemApiAdapter
from .patch import PatchApiAdapter
from .projects import ProjectApiAdapter
from .tasks import TaskApiAdapter
from .ui import UiApiAdapter

__all__ = [
    "CommandApiAdapter",
    "CoreApiAdapter",
    "DocumentApiAdapter",
    "EventApiAdapter",
    "ItemApiAdapter",
    "PatchApiAdapter",
    "ProjectApiAdapter",
    "TaskApiAdapter",
    "UiApiAdapter",
    "build_module_context",
]
