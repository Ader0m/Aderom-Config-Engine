# -*- coding: utf-8 -*-
"""Доменные фичи предметов: каталог, иконки, локализация и создание."""

from .catalog import (
    DEFAULT_ICON_TEXTURE,
    GRID_SIZE,
    ITEM_CATALOG_VERSION,
    ItemCatalog,
    ItemCatalogService,
    ItemField,
    ItemRecord,
    ItemSection,
)
from .icons import ICON_CACHE_VERSION, ItemIconService
from core.api.localization import (
    LOCALIZATION_VERSION,
    LocalizationEntry,
    LocalizationService,
    LocalizationTable,
    edit_localization_text,
)

from .service import ItemFeatureService

__all__ = [
    "ItemFeatureService",
    "DEFAULT_ICON_TEXTURE",
    "GRID_SIZE",
    "ITEM_CATALOG_VERSION",
    "ItemCatalog",
    "ItemCatalogService",
    "ItemField",
    "ItemRecord",
    "ItemSection",
    "ICON_CACHE_VERSION",
    "ItemIconService",
    "LOCALIZATION_VERSION",
    "LocalizationEntry",
    "LocalizationService",
    "LocalizationTable",
    "edit_localization_text",
]
