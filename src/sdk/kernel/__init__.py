# -*- coding: utf-8 -*-
"""Ядро SDK: модуль, контекст, регистрации, версия, capabilities."""

from .api import CommandApi, EventApi, TaskApi
from .capabilities import (
    Capability,
    CapabilityConflictError,
    CapabilityError,
    CapabilityRegistry,
    CapabilityScope,
    CapabilityUnavailable,
    CapabilityVersionError,
    MissingCapability,
)
from .context import KernelApi, ModuleContext
from .levels import ApiLevel, api_level, is_stable
from .module import (
    ModuleBase,
    ModuleManifest,
    ModuleRequirement,
    normalize_requirement,
    normalize_requirements,
)
from .registrations import RegistrationHandle, RegistrationScope
from .services import CoreApi
from .version import SDK_VERSION, is_compatible, parse_version, version_at_least

__all__ = [
    "SDK_VERSION",
    "parse_version",
    "is_compatible",
    "version_at_least",
    "ModuleManifest",
    "ModuleRequirement",
    "ModuleBase",
    "ModuleContext",
    "KernelApi",
    "normalize_requirement",
    "normalize_requirements",
    "RegistrationHandle",
    "RegistrationScope",
    "Capability",
    "CapabilityConflictError",
    "CapabilityError",
    "CapabilityRegistry",
    "CapabilityScope",
    "CapabilityUnavailable",
    "CapabilityVersionError",
    "MissingCapability",
    "ApiLevel",
    "api_level",
    "is_stable",
    "CoreApi",
    "TaskApi",
    "EventApi",
    "CommandApi",
]
