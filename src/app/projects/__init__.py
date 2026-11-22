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
from .service import (
    AssetChange,
    PreviewItem,
    Project,
    ProjectAssetDraft,
    ProjectDraft,
    ProjectError,
    ProjectInfo,
    ProjectPatchPreview,
    ProjectResolvedDraft,
    ProjectService,
    SavedDraft,
    normalize_save_mode,
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
    "AssetChange",
    "FileReaderError",
    "FileReaderSubscription",
    "PreviewItem",
    "PROJECT_SCHEMA_VERSION",
    "Project",
    "ProjectAssetDraft",
    "ProjectAssetRegistry",
    "ProjectAssetTypeError",
    "ProjectDraft",
    "ProjectError",
    "ProjectFileReaderRegistry",
    "ProjectInfo",
    "ProjectPatchPreview",
    "ProjectRepository",
    "ProjectResolvedDraft",
    "ProjectService",
    "ProjectSettingError",
    "ProjectSettingsRegistry",
    "SavedDraft",
    "normalize_save_mode",
]
