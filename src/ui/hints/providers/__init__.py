# -*- coding: utf-8 -*-
"""Платформенные провайдеры подсказок."""

from .colors import ColorTagProvider
from .includes import DirectiveProvider, IncludePathProvider
from .keys import KeyProvider
from .localization import LocalizationKeyProvider
from .sections import ParentProvider, SectionProvider

__all__ = [
    "ColorTagProvider",
    "DirectiveProvider",
    "IncludePathProvider",
    "KeyProvider",
    "LocalizationKeyProvider",
    "ParentProvider",
    "SectionProvider",
]
