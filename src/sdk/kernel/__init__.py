# -*- coding: utf-8 -*-
"""Ядро SDK: модуль, контекст, регистрации, версия, capabilities."""

from .api import CommandApi, EventApi, TaskApi
from .capabilities import (
    CapabilitiesApi,
    Capability,
    CapabilityConflictError,
    CapabilityError,
    CapabilityUnavailable,
    CapabilityVersionError,
    MissingCapability,
)
from .context import KernelApi, ModuleContext
from .levels import ApiLevel, api_level, is_stable
from .module import (
    MODULE_MANIFEST_VERSION,
    CapabilityDeclaration,
    CapabilityRequirement,
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
    "MODULE_MANIFEST_VERSION",
    "ModuleManifest",
    "ModuleRequirement",
    "CapabilityDeclaration",
    "CapabilityRequirement",
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
    "CapabilityUnavailable",
    "CapabilityVersionError",
    "MissingCapability",
    "CapabilitiesApi",
    "ApiLevel",
    "api_level",
    "is_stable",
    "CoreApi",
    "TaskApi",
    "EventApi",
    "CommandApi",
]
