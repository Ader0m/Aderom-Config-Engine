# -*- coding: utf-8 -*-
"""Порт локализации: таблицы, сервис и индекс."""

from core.catalogs.localization import LocalizationIndex
from core.localization import (
    LOCALIZATION_VERSION,
    LocalizationEntry,
    LocalizationService,
    LocalizationTable,
    edit_localization_text,
)

__all__ = [
    "LOCALIZATION_VERSION",
    "LocalizationEntry",
    "LocalizationIndex",
    "LocalizationService",
    "LocalizationTable",
    "edit_localization_text",
]
