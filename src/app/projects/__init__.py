# -*- coding: utf-8 -*-
"""Проект как прикладной сценарий, без зависимости от Qt."""

from .assets import (
    ProjectAssetRegistry,
    ProjectAssetTypeError,
)
from .readers import (
    FileReaderError,
    FileReaderSubscription,
    ProjectFileReaderRegistry,
)
from .models import (
    Project,
    ProjectAssetDraft,
    ProjectDraft,
    ProjectError,
    ProjectInfo,
    ProjectResolvedDraft,
    normalize_save_mode,
)
from .service import (
    ProjectService,
    SavedDraft,
)
from .repository import (
    PROJECT_SCHEMA_VERSION,
    ProjectRepository,
)
from .settings import (
    ProjectSettingError,
    ProjectSettingsRegistry,
)

__all__ = [
    "FileReaderError",
    "FileReaderSubscription",
    "PROJECT_SCHEMA_VERSION",
    "Project",
    "ProjectAssetDraft",
    "ProjectAssetRegistry",
    "ProjectAssetTypeError",
    "ProjectDraft",
    "ProjectError",
    "ProjectFileReaderRegistry",
    "ProjectInfo",
    "ProjectRepository",
    "ProjectResolvedDraft",
    "ProjectService",
    "ProjectSettingError",
    "ProjectSettingsRegistry",
    "SavedDraft",
    "normalize_save_mode",
]
