# -*- coding: utf-8 -*-
"""Уровни публичного API SDK.

До 1.0 ``deprecated`` не используется: старые имена удаляются сразу.
"""

from __future__ import annotations

from enum import Enum


class ApiLevel(str, Enum):
    STABLE = "stable"
    EXPERIMENTAL = "experimental"
    INTERNAL = "internal"


API_LEVELS = {
    "ModuleBase": ApiLevel.STABLE,
    "ModuleManifest": ApiLevel.STABLE,
    "ModuleRequirement": ApiLevel.STABLE,
    "ModuleContext": ApiLevel.STABLE,
    "CapabilitiesApi": ApiLevel.STABLE,
    "Capability": ApiLevel.STABLE,
    "CoreApi": ApiLevel.STABLE,
    "UiApi": ApiLevel.STABLE,
    "TaskApi": ApiLevel.STABLE,
    "EventApi": ApiLevel.STABLE,
    "CommandApi": ApiLevel.STABLE,
    "DocumentApi": ApiLevel.EXPERIMENTAL,
    "DocumentViewerApi": ApiLevel.EXPERIMENTAL,
    "PatchApi": ApiLevel.EXPERIMENTAL,
    "ItemCatalogApi": ApiLevel.EXPERIMENTAL,
    "ItemSectionDropApi": ApiLevel.EXPERIMENTAL,
    "SectionGraphApi": ApiLevel.EXPERIMENTAL,
    "ProjectAssetsApi": ApiLevel.EXPERIMENTAL,
    "OggEditorApi": ApiLevel.EXPERIMENTAL,
}


def api_level(name) -> ApiLevel:
    """Уровень API по имени. Неизвестное имя считается internal."""
    return API_LEVELS.get(str(name), ApiLevel.INTERNAL)


def is_stable(name) -> bool:
    return api_level(name) is ApiLevel.STABLE


def check_manifest(manifest, sdk_version) -> bool:
    """Проверить sdk_min и необязательный sdk_max манифеста."""
    from .version import parse_version

    minimum = parse_version(getattr(manifest, "sdk_min", "") or "0.0.0")
    maximum = parse_version(getattr(manifest, "sdk_max", "") or "999.0.0")
    current = parse_version(sdk_version)
    return minimum <= current <= maximum
