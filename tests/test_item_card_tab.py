# -*- coding: utf-8 -*-
"""Карточка предмета как вкладка основного окна."""

import unittest

from tests.support import SRC  # noqa: F401

from tests.qt_support import qapp
from sdk.api import ItemEntry, ItemField
from ui.bus import EventBus
from app.documents import ResolvedDocument
from ui.editor_tabs import EditorTabs
from modules.base_module.item_viewer import ItemCardView, ItemViewerFeature

_app = qapp()


class _Ui:
    def __init__(self, tabs, document):
        self.tabs = tabs
        self.document = document
        self.errors = []

    def open_editor_tab(self, tab_id, title, widget):
        self.tabs.open_tab(tab_id, title, widget)

    def retain_resolved_document(self, tab_id):
        if tab_id != self.document.tab_id:
            return False
        self.document.retain()
        return True

    def release_resolved_document(self, tab_id):
        if tab_id != self.document.tab_id:
            return False
        self.document.release()
        return True

    def resolved_draft(self, tab_id):
        if tab_id != self.document.tab_id:
            return None
        return self.document.snapshot()

    def item_entry(self, tab_id, section):
        if tab_id != self.document.tab_id:
            return None
        text = self.document.current_text
        if "[%s]" % section not in text:
            return None
        value = "i_med" if "kind = i_med" in text else "i_food"
        return ItemEntry(
            section=section,
            fields=(ItemField("kind", value, 2),),
        )

    def apply_resolved_field_edit(self, tab_id, section, key, value):
        if tab_id != self.document.tab_id:
            return False
        return self.document.apply_field_edit(section, key, value)

    def apply_resolved_line_edit(self, tab_id, action, line_number, text=""):
        if tab_id != self.document.tab_id:
            return False
        return self.document.apply_line_edit(action, line_number, text)

    def is_project_active(self):
        return True

    def save_project_now(self):
        return 1

    def undo_resolved_document(self, tab_id):
        return self.document.undo()

    def redo_resolved_document(self, tab_id):
        return self.document.redo()

    def open_resolved_document(self, tab_id, line_number=None):
        return True

    def show_error(self, message):
        self.errors.append(str(message))

    def set_status(self, *args, **kwargs):
        pass

    def log(self, message):
        pass

    def theme_color(self, name, fallback=None):
        return fallback


class _Ctx:
    def __init__(self, events, ui):
        self.events = events
        self.ui = ui
        self.services = None


class ItemCardTabTest(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()
        self.tabs = EditorTabs(events=self.bus)
        self.document = ResolvedDocument(
            "base.resolved:system.ltx",
            "system.ltx",
            "[Res]system.ltx",
            "[item]\nkind = i_food\ncost = 10\n",
            line_sources=(None, None, None),
            events=self.bus,
        )
        self.ui = _Ui(self.tabs, self.document)
        self.feature = ItemViewerFeature(
            _Ctx(self.bus, self.ui),
            session=None,
        )
        self.feature._tab_id = self.document.tab_id
        self.feature._root_path = self.document.root_path
        self.feature._title = self.document.title
        self.feature._resolved_text = self.document.current_text
        self.feature._line_sources = self.document.line_sources

    def tearDown(self):
        self.feature.close()
        self.tabs.close()

    def test_open_card_creates_tab_and_retains_document(self):
        self.feature.open_card(self.document.tab_id, "item")

        card_tab = self.feature._card_tab_id(
            self.document.tab_id,
            "item",
        )
        self.assertTrue(self.tabs.has_tab(card_tab))
        self.assertEqual(self.tabs.tabText(self.tabs.indexOf(
            self.tabs.get_tab(card_tab)
        )), "[item]item")
        self.assertIsInstance(self.tabs.get_tab(card_tab), ItemCardView)
        self.assertEqual(self.document.client_count, 1)

    def test_card_refreshes_after_document_change(self):
        self.feature.open_card(self.document.tab_id, "item")
        card_tab = self.feature._card_tab_id(
            self.document.tab_id,
            "item",
        )
        card = self.tabs.get_tab(card_tab)
        card.refresh()
        self.assertEqual(card.fields_table.item(0, 1).text(), "i_food")

        self.document.apply_field_edit("item", "kind", "i_med")
        self.feature._flush_refresh()

        self.assertEqual(card.fields_table.item(0, 1).text(), "i_med")
        self.assertEqual(self.ui.errors, [])

    def test_close_card_releases_document(self):
        self.feature.open_card(self.document.tab_id, "item")
        card_tab = self.feature._card_tab_id(
            self.document.tab_id,
            "item",
        )
        self.tabs.close_tab(card_tab)
        self.assertEqual(self.document.client_count, 0)
        self.assertFalse(self.tabs.has_tab(card_tab))

    def test_open_same_card_twice_focuses_existing(self):
        self.feature.open_card(self.document.tab_id, "item")
        self.feature.open_card(self.document.tab_id, "item")
        card_tab = self.feature._card_tab_id(
            self.document.tab_id,
            "item",
        )
        self.assertEqual(self.tabs.current_tab_id, card_tab)
        self.assertEqual(self.document.client_count, 1)

    def test_tab_undo_routes_to_document(self):
        self.feature.open_card(self.document.tab_id, "item")
        self.document.apply_field_edit("item", "kind", "i_med")
        self.assertIn("kind = i_med", self.document.current_text)
        self.tabs.undo()
        self.assertIn("kind = i_food", self.document.current_text)


if __name__ == "__main__":
    unittest.main()
