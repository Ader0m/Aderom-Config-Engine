# -*- coding: utf-8 -*-
"""SDK — единственный публичный контракт между модулями и приложением."""

from .version import SDK_VERSION, parse_version, is_compatible
from .contracts import ModuleManifest, ModuleBase, ModuleContext
from .events import Events
from .loader import ModuleLoader, ModuleLoadError

__all__ = [
    "SDK_VERSION",
    "parse_version",
    "is_compatible",
    "ModuleManifest",
    "ModuleBase",
    "ModuleContext",
    "Events",
    "ModuleLoader",
    "ModuleLoadError",
]