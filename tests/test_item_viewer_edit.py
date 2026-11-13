# -*- coding: utf-8 -*-
"""Правка resolved-строк из карточки предмета."""

import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support import SRC  # noqa: F401

from PyQt5.QtCore import QPoint
from PyQt5.QtWidgets import QMenu

from tests.qt_support import qapp
from sdk.api import ItemCatalog, ItemEntry, ItemField
from ui.documents import ResolvedDocument
from modules.base_module.item_viewer import (
    ItemCardView,
    ItemViewerFeature,
    ItemViewerWindow,
)

_app = qapp()


class _Ui:
    def __init__(self):
        self.document = None
        self.errors = []
        self.calls = []

    def is_project_active(self):
        return True

    def resolved_draft(self, tab_id):
        if self.document is None or tab_id != self.document.tab_id:
            return None
        return self.document.snapshot()

    def apply_resolved_line_edit(self, tab_id, action, line_number, text=""):
        if self.document is None or tab_id != self.document.tab_id:
            return False
        self.calls.append((action, line_number, text))
        return self.document.apply_line_edit(action, line_number, text)

    def item_entry(self, tab_id, section):
        if self.document is None or tab_id != self.document.tab_id:
            return None
        if section.lower() != "item":
            return None
        text = self.document.current_text
        value = "i_food"
        if "kind = i_med" in text:
            value = "i_med"
        return ItemEntry(
            section="item",
            fields=(ItemField("kind", value, 2),),
        )

    def sync_resolved_tab(self, *args):
        pass

    def open_resolved_tab(self, *args, **kwargs):
        pass

    def open_resolved_document(self, *args, **kwargs):
        pass

    def save_project_now(self):
        return 1

    def undo_resolved_document(self, tab_id):
        return False

    def redo_resolved_document(self, tab_id):
        return False

    def show_error(self, message):
        self.errors.append(str(message))

    def set_status(self, *args, **kwargs):
        pass

    def log(self, message):
        pass

    def theme_color(self, name, fallback=None):
        return fallback


class _Ctx:
    def __init__(self):
        self.ui = _Ui()
        self.events = None
        self.services = None


class _Session:
    config_dir = None
    forest = None


class ItemLineEditTest(unittest.TestCase):
    def setUp(self):
        self.ctx = _Ctx()
        self.document = ResolvedDocument(
            "base.resolved:system.ltx",
            Path("system.ltx"),
            "[Res]system.ltx",
            "[item]\nkind = i_food\ncost = 10\n",
            line_sources=(None, None, None),
        )
        self.ctx.ui.document = self.document
        self.feature = ItemViewerFeature(self.ctx, _Session())
        self.feature._root_path = Path("system.ltx")
        self.feature._tab_id = self.document.tab_id
        self.feature._title = "[Res]system.ltx"
        self.feature._resolved_text = self.document.current_text
        self.feature._line_sources = self.document.line_sources

    def test_replace_line_goes_through_document(self):
        self.assertTrue(
            self.feature._replace_line(
                self.document.tab_id,
                2,
                "kind = i_med",
            )
        )
        self.assertIn("kind = i_med", self.document.current_text)
        self.assertEqual(
            self.ctx.ui.calls,
            [("replace", 2, "kind = i_med")],
        )

    def test_insert_after_line_goes_through_document(self):
        self.assertTrue(
            self.feature._insert_line(
                self.document.tab_id,
                2,
                "weight = 1",
            )
        )
        lines = self.document.current_text.split("\n")
        self.assertEqual(lines[2], "weight = 1")
        self.assertEqual(
            len(self.document.line_sources),
            self.document.line_count,
        )

    def test_delete_line_goes_through_document(self):
        self.assertTrue(
            self.feature._delete_line(self.document.tab_id, 2)
        )
        self.assertNotIn("kind = i_food", self.document.current_text)
        self.assertEqual(
            len(self.document.line_sources),
            self.document.line_count,
        )

    def test_external_change_does_not_block_edit(self):
        # Документ изменился в другой поверхности (например, в ltx).
        self.document.apply_line_edit("replace", 3, "cost = 99")
        self.assertTrue(
            self.feature._replace_line(
                self.document.tab_id,
                2,
                "kind = i_med",
            )
        )
        self.assertIn("kind = i_med", self.document.current_text)
        self.assertIn("cost = 99", self.document.current_text)
        self.assertEqual(self.ctx.ui.errors, [])

    def test_invalid_line_reports_error(self):
        self.assertFalse(
            self.feature._replace_line(
                self.document.tab_id,
                99,
                "kind = i_med",
            )
        )
        self.assertTrue(self.ctx.ui.errors)


class ItemCardViewTest(unittest.TestCase):
    def test_field_edit_uses_callback_and_refreshes(self):
        entry = ItemEntry(
            section="item",
            fields=(
                ItemField("kind", "i_food", 2),
                ItemField("cost", "10", 3),
            ),
        )
        calls = []

        def replace_line(line, text):
            calls.append((line, text))
            return True

        card = ItemCardView(
            get_entry=lambda: entry,
            get_image=lambda section: None,
            can_edit=lambda: True,
            on_replace_line=replace_line,
        )
        card.refresh()

        self.assertEqual(card.fields_table.rowCount(), 2)
        card.fields_table.item(0, 1).setText("i_med")

        self.assertEqual(calls, [(2, "kind = i_med")])


class ItemViewerWindowSmokeTest(unittest.TestCase):
    def test_window_creates_card_preview(self):
        window = ItemViewerWindow(
            can_edit=lambda: True,
            on_item_entry=lambda section: None,
        )
        self.assertIsNotNone(window.card)
        self.assertTrue(window.card._preview)

    def test_context_menu_opens_item_tab(self):
        opened = []
        window = ItemViewerWindow(
            can_edit=lambda: True,
            on_item_entry=lambda section: None,
            on_open_card=opened.append,
        )
        item = ItemEntry(
            section="item",
            fields=(ItemField("kind", "i_food", 2),),
        )
        window.set_catalog(
            ItemCatalog(items=(item,), source_path=Path("system.ltx")),
            {},
        )
        window.resize(800, 500)
        window.show()
        _app.processEvents()

        index = window.proxy.index(0, 0)
        rect = window.list_view.visualRect(index)
        if rect.isValid() and not rect.isEmpty():
            position = window.list_view.viewport().mapTo(
                window.list_view,
                rect.center(),
            )
        else:
            position = QPoint(20, 20)

        def fake_exec(menu, *args, **kwargs):
            for action in menu.actions():
                if action.text() == "Открыть в новой вкладке":
                    return action
            return None

        with patch.object(QMenu, "exec_", fake_exec):
            window._on_item_context_menu(position)

        self.assertEqual(opened, ["item"])
        window.close()

    def test_context_menu_without_callback_does_not_show(self):
        window = ItemViewerWindow(can_edit=lambda: True)
        with patch.object(QMenu, "exec_") as exec_mock:
            window._on_item_context_menu(QPoint(10, 10))
        exec_mock.assert_not_called()
        window.close()


if __name__ == "__main__":
    unittest.main()
