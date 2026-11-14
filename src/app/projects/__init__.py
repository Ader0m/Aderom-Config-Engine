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
from .settings import (
    ProjectSettingError,
    ProjectSettingsRegistry,
)

__all__ = [
    "AssetChange",
    "FileReaderError",
    "FileReaderSubscription",
    "PreviewItem",
    "Project",
    "ProjectAssetDraft",
    "ProjectAssetRegistry",
    "ProjectAssetTypeError",
    "ProjectDraft",
    "ProjectError",
    "ProjectFileReaderRegistry",
    "ProjectInfo",
    "ProjectPatchPreview",
    "ProjectResolvedDraft",
    "ProjectService",
    "ProjectSettingError",
    "ProjectSettingsRegistry",
    "SavedDraft",
    "normalize_save_mode",
]
