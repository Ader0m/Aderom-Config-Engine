# -*- coding: utf-8 -*-
"""Каталоги данных и локализация ядра."""

from __future__ import annotations

from core.catalogs import (
    ColorCatalog,
    IncludeCatalog,
    LocalizationIndex,
    SectionCatalog,
)


class DataServices:
    """Цвета, секции, include-пути и локализация."""

    def __init__(
        self,
        localization,
        localization_index=None,
        colors=None,
        sections=None,
        includes=None,
    ):
        self.localization = localization
        self.localization_index = localization_index or LocalizationIndex()
        self.colors = colors or ColorCatalog()
        self.sections = sections or SectionCatalog()
        self.includes = includes or IncludeCatalog()

    def color_definitions(self, forest, overlay=None, config_dir=None):
        """Цвета ``%c[name]`` как ``{name: (r, g, b, a)}``."""
        value = self.colors.load(
            forest,
            overlay=overlay,
            config_dir=config_dir,
        )
        return {item.name: item.rgba for item in value.entries}

    def color_set(self, forest, overlay=None, config_dir=None):
        return self.colors.load(
            forest,
            overlay=overlay,
            config_dir=config_dir,
        )

    def section_set(self, forest, overlay=None, deadline=None):
        return self.sections.build(
            forest,
            overlay=overlay,
            deadline=deadline,
        )

    def include_set(self, forest, overlay=None):
        return self.includes.load(forest, overlay=overlay)

    def localization_set(self, forest, overlay=None, log=None):
        table = self.localization.load(
            forest,
            config_dir=(getattr(forest, "scan_dir", None) if forest else None),
            overlay=overlay,
            log=log,
        )
        return self.localization_index.build(table)

    def clear_memory(self):
        """Очистить память каталогов и таблицы локализации."""
        clear = getattr(self.localization, "clear_memory", None)
        if callable(clear):
            clear()
        for catalog in (
            self.colors,
            self.sections,
            self.includes,
            self.localization_index,
        ):
            clear = getattr(catalog, "clear", None)
            if callable(clear):
                clear()


__all__ = ["DataServices"]
