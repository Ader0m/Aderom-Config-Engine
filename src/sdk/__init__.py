# -*- coding: utf-8 -*-
"""SDK — единственный публичный контракт между модулями и приложением."""

from .version import SDK_VERSION, parse_version, is_compatible
from .contracts import ModuleManifest, ModuleBase, ModuleContext, FileOpenHandler
from .events import Events
from .api import (
    CoreApi,
    UiApi,
    TaskApi,
    EventApi,
    CommandApi,
    TreeSummary,
    GraphMatch,
    GraphNode,
    SectionGraph,
    XmlResolveResult,
    LineSource,
    ItemCatalog,
    ItemEntry,
    ItemField,
    ItemIcon,
    TextureRegion,
)
from .adapters import (
    CommandApiAdapter,
    CoreApiAdapter,
    EventApiAdapter,
    TaskApiAdapter,
    UiApiAdapter,
)

__all__ = [
    "SDK_VERSION",
    "parse_version",
    "is_compatible",
    "ModuleManifest",
    "ModuleBase",
    "ModuleContext",
    "Events",
    "FileOpenHandler",
    "CoreApi",
    "UiApi",
    "TaskApi",
    "EventApi",
    "CommandApi",
    "TreeSummary",
    "GraphMatch",
    "GraphNode",
    "SectionGraph",
    "XmlResolveResult",
    "LineSource",
    "ItemCatalog",
    "ItemEntry",
    "ItemField",
    "ItemIcon",
    "TextureRegion",
    "CoreApiAdapter",
    "EventApiAdapter",
    "CommandApiAdapter",
    "TaskApiAdapter",
    "UiApiAdapter",
]
