# -*- coding: utf-8 -*-
"""ItemCatalogApiAdapter: каталог предметов и item-проекции."""

from __future__ import annotations

from pathlib import Path

from sdk.contracts.forest import unwrap_forest
from sdk.features.items.models import (
    ItemCatalog,
    ItemEntry,
    ItemIcon,
    ItemSection,
    TextureRegion,
)

from .common import _to_item_entry


class ItemCatalogApiAdapter:
    """Реализация :class:`sdk.features.items.ItemCatalogApi`."""

    def __init__(self, core_provider, platform=None):
        self._provider = core_provider
        self._platform = platform

    def item_catalog_root(self, forest):
        raw = self._provider.item_catalog_root(unwrap_forest(forest))
        return Path(raw) if raw is not None else None

    def build_item_catalog(
        self,
        forest,
        log=None,
        resolved_text=None,
        reuse_snapshot_cache=False,
        overlay=None,
    ):
        raw = self._provider.build_item_catalog(
            unwrap_forest(forest),
            log=log,
            resolved_text=resolved_text,
            reuse_snapshot_cache=reuse_snapshot_cache,
            overlay=overlay,
        )

        source_path = getattr(raw, "source_path", None)
        if source_path is not None:
            source_path = Path(source_path)

        items = []
        for item in (getattr(raw, "items", ()) or ()):
            items.append(_to_item_entry(item))

        sections = tuple(
            ItemSection(
                name=str(getattr(section, "name", "")),
                fields=tuple(
                    str(field)
                    for field in (getattr(section, "fields", ()) or ())
                ),
                parents=tuple(
                    str(parent)
                    for parent in (getattr(section, "parents", ()) or ())
                ),
            )
            for section in (getattr(raw, "sections", ()) or ())
        )

        return ItemCatalog(
            source_path=source_path,
            items=tuple(items),
            warnings=tuple(getattr(raw, "warnings", ()) or ()),
            cache_key=str(getattr(raw, "cache_key", "") or ""),
            sections=sections,
            parent_names=tuple(
                str(name)
                for name in (getattr(raw, "parent_names", ()) or ())
            ),
        )

    def build_item_entry(
        self,
        forest,
        resolved_text,
        section,
        log=None,
        overlay=None,
    ):
        raw = self._provider.build_item_entry(
            unwrap_forest(forest),
            resolved_text,
            section,
            log=log,
            overlay=overlay,
        )
        if raw is None:
            return None
        return _to_item_entry(raw)

    def build_item_entry_from_block(
        self,
        forest,
        block_text,
        section,
        offset=0,
        log=None,
        overlay=None,
    ):
        raw = self._provider.build_item_entry_from_block(
            unwrap_forest(forest),
            block_text,
            section,
            offset=offset,
            log=log,
            overlay=overlay,
        )
        if raw is None:
            return None
        return _to_item_entry(raw)

    def edit_localization_text(self, text, key, value):
        return self._provider.edit_localization_text(text, key, value)

    def read_texture_region(self, path, x, y, width, height):
        raw = self._provider.read_texture_region(path, x, y, width, height)
        return TextureRegion(
            width=int(raw.width),
            height=int(raw.height),
            rgba=bytes(raw.rgba),
        )

    def load_item_icons(self, catalog, log=None, progress=None):
        requests = []
        for entry in (getattr(catalog, "items", ()) or ()):
            if entry.icon_path is None:
                continue

            x, y, width, height = entry.icon_rect
            if x < 0 or y < 0 or width <= 0 or height <= 0:
                continue

            requests.append((
                entry.section,
                str(entry.icon_path),
                x,
                y,
                width,
                height,
            ))

        raw = self._provider.load_item_icons(
            getattr(catalog, "cache_key", "") or "",
            requests,
            log=log,
            progress=progress,
        )

        return tuple(
            ItemIcon(
                section=str(section),
                width=int(region.width),
                height=int(region.height),
                rgba=bytes(region.rgba),
            )
            for section, region in (raw or ())
        )

    def item_entry(self, tab_id, section):
        raw = self._platform.item_entry(tab_id, section)
        if raw is None:
            return None
        return _to_item_entry(raw)

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
        return self._provider.plan_item_creation(
            item_text,
            xml_text,
            known_sections,
            section,
            parent,
            parent_fields,
            project_name,
        )

    def creation_paths(self, scan_dir, project_name):
        return self._provider.creation_paths(scan_dir, project_name)

    def parent_fields(self, catalog, parent):
        return self._provider.parent_fields(catalog, parent)
