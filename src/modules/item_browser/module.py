# -*- coding: utf-8 -*-
"""Модуль просмотра предметов.

Предоставляет capability ``items.catalog`` и ``item.section.dragdrop``.
Доменная сборка каталога остаётся в платформенном ``ctx.items``;
модуль владеет только UI каталога, карточек и drag-and-drop.
"""

from __future__ import annotations

from pathlib import Path

from sdk import ModuleBase
from sdk.contracts.events import Events

from .item_viewer import ItemViewerFeature
from .session import ItemSession


class _ItemCatalogProvider:
    """Реализация ``ItemCatalogApi`` поверх платформенного каталога."""

    def __init__(self, api):
        self._api = api

    def item_catalog_root(self, forest):
        return self._api.item_catalog_root(forest)

    def build_item_catalog(
        self,
        forest,
        log=None,
        resolved_text=None,
        reuse_snapshot_cache=False,
        overlay=None,
    ):
        return self._api.build_item_catalog(
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
        return self._api.build_item_entry(
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
        return self._api.build_item_entry_from_block(
            forest,
            block_text,
            section,
            offset=offset,
            log=log,
            overlay=overlay,
        )

    def edit_localization_text(self, text, key, value):
        return self._api.edit_localization_text(text, key, value)

    def read_texture_region(self, path, x, y, width, height):
        return self._api.read_texture_region(path, x, y, width, height)

    def load_item_icons(self, catalog, log=None, progress=None):
        return self._api.load_item_icons(
            catalog,
            log=log,
            progress=progress,
        )

    def item_entry(self, tab_id, section):
        return self._api.item_entry(tab_id, section)

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
        return self._api.plan_item_creation(
            item_text,
            xml_text,
            known_sections,
            section,
            parent,
            parent_fields,
            project_name,
        )

    def creation_paths(self, scan_dir, project_name):
        return self._api.creation_paths(scan_dir, project_name)

    def parent_fields(self, catalog, parent):
        return self._api.parent_fields(catalog, parent)


class _ItemSectionDropProvider:
    """Реализация ``ItemSectionDropApi`` для предметных секций."""

    def __init__(self, feature):
        self._feature = feature

    def accept_item_section(self, data) -> bool:
        return self._feature.accept_item_section(data)


class ItemBrowserModule(ModuleBase):
    ITEM_VIEWER_COMMAND = "item_browser.item_viewer"
    OPEN_ITEM_CARD_COMMAND = "item_browser.open_item_card"

    def init(self, ctx):
        super().init(ctx)
        self.session = ItemSession(ctx)
        self._item_viewer = ItemViewerFeature(
            ctx,
            self.session,
            attach_hints=ctx.ui.attach_text_hints,
        )
        self._subs = [
            ctx.events.subscribe(
                Events.CLEAN_RUN_STARTED,
                self._on_clean_run_started,
            ),
            ctx.events.subscribe(
                Events.UI_THEME_CHANGED,
                self._on_theme_changed,
            ),
        ]

    def _on_clean_run_started(self, payload=None):
        if payload:
            self.session.config_dir = Path(str(payload))

    def _on_theme_changed(self, _payload=None):
        try:
            self._item_viewer.refresh_theme()
        except Exception:
            self.ctx.log("item_browser: не удалось обновить тему")

    def register(self, ctx):
        ctx.capabilities.provide(
            "items.catalog",
            "1.0.0",
            _ItemCatalogProvider(ctx.items),
        )
        ctx.capabilities.provide(
            "item.section.dragdrop",
            "1.0.0",
            _ItemSectionDropProvider(self._item_viewer),
        )
        ctx.ui.register_menu(
            "Инструменты",
            "Просмотр игровых предметов",
            self._item_viewer.show,
            "Ctrl+I",
        )
        ctx.ui.register_menu(
            "Инструменты",
            "Открыть карточку предмета под курсором",
            self._open_item_card_at_cursor,
            "Ctrl+Shift+I",
        )
        ctx.commands.register(
            self.ITEM_VIEWER_COMMAND,
            self._item_viewer.show,
        )
        ctx.commands.register(
            self.OPEN_ITEM_CARD_COMMAND,
            self._open_item_card_at_cursor,
        )

    def activate(self, ctx):
        ctx.log("Модуль просмотра предметов активирован.")

    def deactivate(self, ctx):
        for sub in self._subs:
            try:
                sub.unsubscribe()
            except Exception:
                pass
        self._subs.clear()
        self._item_viewer.close()
        ctx.log("Модуль просмотра предметов деактивирован.")

    def _open_item_card_at_cursor(self):
        return bool(
            self.ctx.commands.execute("ui.item_card_at_cursor")
        )


MODULE = ItemBrowserModule()
