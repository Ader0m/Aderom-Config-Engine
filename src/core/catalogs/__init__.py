# -*- coding: utf-8 -*-
"""Каталоги данных для подсказок и других потребителей ядра."""

from .colors import (
    ColorCatalog,
    ColorDef,
    ColorSet,
    EMPTY_COLORS,
    parse_color_definitions,
)
from .includes import (
    IncludeCatalog,
    IncludeEntry,
    IncludeSet,
    EMPTY_INCLUDES,
)
from .localization import (
    LocalizationIndex,
    LocalizationSet,
    EMPTY_LOCALIZATION,
)
from .sections import (
    SectionCatalog,
    SectionInfo,
    SectionSet,
    EMPTY_SECTIONS,
)

__all__ = [
    "ColorCatalog",
    "ColorDef",
    "ColorSet",
    "EMPTY_COLORS",
    "parse_color_definitions",
    "IncludeCatalog",
    "IncludeEntry",
    "IncludeSet",
    "EMPTY_INCLUDES",
    "LocalizationIndex",
    "LocalizationSet",
    "EMPTY_LOCALIZATION",
    "SectionCatalog",
    "SectionInfo",
    "SectionSet",
    "EMPTY_SECTIONS",
]
