# -*- coding: utf-8 -*-
"""Платформенные провайдеры подсказок."""

from .colors import ColorTagProvider
from .includes import DirectiveProvider, IncludePathProvider
from .keys import KeyProvider
from .localization import LocalizationKeyProvider
from .parents import ParentNameProvider
from .sections import ParentProvider, SectionProvider

__all__ = [
    "ColorTagProvider",
    "DirectiveProvider",
    "IncludePathProvider",
    "KeyProvider",
    "LocalizationKeyProvider",
    "ParentNameProvider",
    "ParentProvider",
    "SectionProvider",
]
