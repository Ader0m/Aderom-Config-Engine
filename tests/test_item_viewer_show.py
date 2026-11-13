# -*- coding: utf-8 -*-
"""Сборка каталога и создание окна просмотра предметов."""

import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from tests.qt_support import qapp
from sdk.api import ItemCatalog, ItemEntry, ItemField, ItemSection
from modules.base_module.item_viewer import ItemViewerFeature

_app = qapp()


class _Ui:
    def __init__(self):
        self.ensured = []
        self.retained = []
        self.released = []
        self.window = None
        self.status = []
        self.logs = []

    def item_catalog_root(self, forest):
        return Path("system.ltx")

    def relative_path(self, forest, root):
        return "system.ltx"

    def assemble_resolved(self, root, forest, config_dir, log=None,
                          overlay=None):
        return "[item]\nkind = i_food\n", (None, None)

    def build_item_catalog(self, forest, log=None, resolved_text=None,
                           reuse_snapshot_cache=False):
        return ItemCatalog(
            source_path=Path("system.ltx"),
            items=(
                ItemEntry(
                    section="item",
                    fields=(ItemField("kind", "i_food", 2),),
                    line_number=1,
                ),
            ),
            sections=(ItemSection(name="item", fields=("kind",)),),
        )

    def load_item_icons(self, catalog, log=None):
        return ()

    def resolved_draft(self, tab_id):
        return None

    def ensure_resolved_document(self, tab_id, root_path, title="", text="",
                                 line_sources=()):
        self.ensured.append((tab_id, Path(root_path), text))
        return True

    def retain_resolved_document(self, tab_id):
        self.retained.append(tab_id)
        return True

    def release_resolved_document(self, tab_id):
        self.released.append(tab_id)
        return True

    def item_entry(self, tab_id, section):
        return None

    def open_editor_tab(self, tab_id, title, widget):
        pass

    def show_module_window(self, key, window):
        self.window = window

    def close_module_window(self, key):
        pass

    def theme_color(self, name, fallback=None):
        return fallback

    def set_status(self, *args, **kwargs):
        self.status.append(args)

    def log(self, message):
        self.logs.append(str(message))

    def is_project_active(self):
        return True


class _Tasks:
    def run(self, work, on_done, on_log=None):
        result = work(lambda message: None)
        on_done(result, None)


class _Session:
    has_tree = True
    forest = object()
    config_dir = None


class _Ctx:
    def __init__(self):
        self.ui = _Ui()
        self.tasks = _Tasks()
        self.events = None
        self.services = self.ui


class ItemViewerShowTest(unittest.TestCase):
    def test_show_builds_catalog_and_creates_window(self):
        ctx = _Ctx()
        feature = ItemViewerFeature(ctx, _Session())
        try:
            feature.show()

            self.assertIsNotNone(feature._window)
            self.assertIs(ctx.ui.window, feature._window)
            self.assertEqual(feature._tab_id, "base.resolved:system.ltx")
            self.assertIn(feature._tab_id, ctx.ui.retained)
            self.assertEqual(
                ctx.ui.ensured[0][0],
                "base.resolved:system.ltx",
            )
            self.assertEqual(
                ctx.ui.ensured[0][2],
                "[item]\nkind = i_food\n",
            )
            self.assertEqual(
                ctx.ui.window.card.section(),
                "item",
            )
        finally:
            feature.close()


if __name__ == "__main__":
    unittest.main()
