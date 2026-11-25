# -*- coding: utf-8 -*-
"""Прикладной сервис предметов: каталог, иконки и локализация.

Доменные правила живут здесь, а не в `core`. Модуль `item_browser`
получает сервис через `ctx.items`; UI не импортирует `app` напрямую.
"""

from __future__ import annotations

from pathlib import Path

from core.api.localization import edit_localization_text

from .catalog import ItemCatalogService
from .creation import (
    creation_paths,
    parent_fields,
    plan_item_creation,
    project_tag,
)
from .icons import ItemIconService


class ItemFeatureService:
    def __init__(
        self,
        *,
        cache_service,
        generated_dir,
        localization,
        localization_index,
        resolver,
    ):
        cache_dir = Path(generated_dir) / "cache-v2" / "items"
        # Локализация — общий сервис композиционного корня: подсказки и
        # карточки предметов используют один кэш и один снимок строк.
        self.localization = localization
        self.localization_index = localization_index
        self.catalog = ItemCatalogService(
            resolver,
            cache_dir=cache_dir,
            localization_service=self.localization,
            max_disk_bytes=256_000_000,
            cache_service=cache_service,
        )
        self.icons = ItemIconService(
            cache_dir=cache_dir,
            max_disk_bytes=256_000_000,
            cache_service=cache_service,
        )

    # -------------------------------------------------- каталог
    def item_catalog_root(self, forest):
        return self.catalog.find_root(forest)

    def build_item_catalog(
        self,
        forest,
        log=None,
        resolved_text=None,
        reuse_snapshot_cache=False,
        overlay=None,
    ):
        return self.catalog.build(
            forest,
            log=log,
            resolved_text=resolved_text,
            reuse_snapshot_cache=reuse_snapshot_cache,
            overlay=overlay,
        )

    def build_item_entry(
        self,
        forest,
        resolved_text,
        section,
        log=None,
        overlay=None,
    ):
        return self.catalog.build_entry(
            forest,
            resolved_text,
            section,
            log=log,
            overlay=overlay,
        )

    def build_item_entry_from_block(
        self,
        forest,
        block_text,
        section,
        offset=0,
        log=None,
        overlay=None,
    ):
        return self.catalog.build_entry_from_block(
            forest,
            block_text,
            section,
            offset=offset,
            log=log,
            overlay=overlay,
        )

    def edit_localization_text(self, text, key, value):
        return edit_localization_text(text, key, value)

    def plan_item_creation(
        self,
        item_text,
        xml_text,
        known_sections,
        section,
        parent,
        parent_fields,
        project_name,
    ):
        return plan_item_creation(
            item_text,
            xml_text,
            known_sections,
            section,
            parent,
            parent_fields,
            project_name,
        )

    def project_tag(self, name):
        return project_tag(name)

    def creation_paths(self, scan_dir, project_name):
        return creation_paths(scan_dir, project_name)

    def parent_fields(self, catalog, parent):
        return parent_fields(catalog, parent)

    # -------------------------------------------------- иконки
    def read_texture_region(self, path, x, y, width, height):
        return self.icons.read_region(path, x, y, width, height)

    def load_item_icons(self, catalog_key, requests, log=None):
        return self.icons.load(
            catalog_key,
            requests,
            log=log,
        )

    # -------------------------------------------------- локализация
    def localization_set(self, forest, overlay=None, log=None):
        table = self.localization.load(
            forest,
            config_dir=(
                getattr(forest, "scan_dir", None) if forest else None
            ),
            overlay=overlay,
            log=log,
        )
        return self.localization_index.build(table)

    def clear_memory(self):
        self.catalog.clear_memory()
        self.icons.clear_memory()
