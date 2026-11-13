# -*- coding: utf-8 -*-
"""Сквозной сценарий: resolved-вкладка + карточка предмета."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.support import SRC  # noqa: F401

from tests.qt_support import qapp
from sdk.api import ItemEntry, ItemField
from ui.bus import EventBus
from ui.editor import ResolvedView
from ui.editor_tabs import EditorTabs
from ui.platform_api import PlatformApi
from modules.base_module.item_viewer import ItemCardView, ItemViewerFeature

_app = qapp()


class _StatusBar:
    def showMessage(self, *_args, **_kwargs):
        pass


class _Window:
    def __init__(self):
        self.editor = EditorTabs(events=EventBus())
        self.logs = []

    def log(self, message):
        self.logs.append(str(message))

    def log_status(self, *_args, **_kwargs):
        pass

    def statusBar(self):
        return _StatusBar()

    def setWindowTitle(self, _title):
        pass

    def set_project_status(self, _status):
        pass

    def _update_project_menu(self):
        pass


class _Services:
    def invalidate_resolver_cache(self):
        return None

    def build_item_entry(self, forest, resolved_text, section, log=None):
        if not section or "[%s]" % section not in resolved_text:
            return None
        value = "i_med" if "kind = i_med" in resolved_text else "i_food"
        return ItemEntry(
            section=section,
            fields=(ItemField("kind", value, 2),),
        )


class _Ctx:
    def __init__(self, events, ui):
        self.events = events
        self.ui = ui
        self.services = _Services()


class ResolvedItemFlowTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.events = EventBus()
        self.commands = SimpleNamespace(register=lambda *a, **k: None)
        paths = SimpleNamespace(
            projects_dir=root / "projects",
            app_dir=root,
        )
        self.api = PlatformApi(
            self.events,
            self.commands,
            None,
            _Services(),
            paths,
        )
        self.window = _Window()
        self.window.editor = EditorTabs(events=self.events)
        self.api.window = self.window
        self.api.project.create("flow", save_mode="root")
        self.api._forest = object()

        self.tab_id = "base.resolved:system.ltx"
        self.api.open_resolved_tab(
            self.tab_id,
            "[Res]system.ltx",
            "[item]\nkind = i_food\ncost = 10\n",
            (None, None, None),
            root / "system.ltx",
        )
        self.view = self.window.editor.get_tab(self.tab_id)
        self.document = self.api.resolved_document(self.tab_id)

        self.feature = ItemViewerFeature(
            _Ctx(self.events, self.api),
            session=None,
        )
        self.feature._tab_id = self.tab_id
        self.feature._root_path = root / "system.ltx"
        self.feature._title = "[Res]system.ltx"
        self.feature._resolved_text = self.document.current_text
        self.feature._line_sources = self.document.line_sources

    def tearDown(self):
        self.feature.close()
        self.window.editor.close()

    def test_open_resolved_tab_binds_document(self):
        self.assertIsInstance(self.view, ResolvedView)
        self.assertIs(self.view.document, self.document)
        self.assertEqual(
            self.window.editor.tabText(
                self.window.editor.indexOf(self.view)
            ),
            "[Res]system.ltx",
        )

    def test_edit_updates_document_and_view(self):
        self.assertTrue(
            self.api.apply_resolved_field_edit(
                self.tab_id,
                "item",
                "kind",
                "i_med",
            )
        )
        self.assertIn("kind = i_med", self.document.current_text)
        self.assertIn("kind = i_med", self.view.text())

    def test_card_tab_reflects_document_edits(self):
        self.feature.open_card(self.tab_id, "item")
        card_tab = self.feature._card_tab_id(self.tab_id, "item")
        card = self.window.editor.get_tab(card_tab)
        self.assertIsInstance(card, ItemCardView)
        card.refresh()
        self.assertEqual(card.fields_table.item(0, 1).text(), "i_food")

        self.api.apply_resolved_field_edit(
            self.tab_id,
            "item",
            "kind",
            "i_med",
        )
        self.feature._flush_refresh()

        self.assertEqual(card.fields_table.item(0, 1).text(), "i_med")
        self.assertIn("kind = i_med", self.view.text())

    def test_typing_uses_focus_line_to_refresh_card(self):
        self.feature.open_card(self.tab_id, "item")
        card_tab = self.feature._card_tab_id(self.tab_id, "item")
        card = self.window.editor.get_tab(card_tab)
        card.refresh()
        self.assertEqual(card.fields_table.item(0, 1).text(), "i_food")

        self.document.replace_text(
            "[item]\nkind = i_med\n",
            focus_line=1,
        )
        self.feature._flush_refresh()

        self.assertEqual(card.fields_table.item(0, 1).text(), "i_med")

    def test_card_edit_updates_resolved_view(self):
        self.feature.open_card(self.tab_id, "item")
        card_tab = self.feature._card_tab_id(self.tab_id, "item")
        card = self.window.editor.get_tab(card_tab)
        card.refresh()

        card.fields_table.item(0, 1).setText("i_med")
        self.assertIn("kind = i_med", self.document.current_text)
        self.assertIn("kind = i_med", self.view.text())

    def test_item_under_cursor_opens_card(self):
        self.view.editor.setCursorPosition(0, 0)
        self.view.open_item_under_cursor()

        card_tab = self.feature._card_tab_id(self.tab_id, "item")
        self.assertTrue(self.window.editor.has_tab(card_tab))
        self.assertEqual(
            self.window.editor.tabText(
                self.window.editor.indexOf(
                    self.window.editor.get_tab(card_tab)
                )
            ),
            "[item]item",
        )

    def test_reopen_clean_document_refreshes_text(self):
        self.api.open_resolved_tab(
            self.tab_id,
            "[Res]system.ltx",
            "[item]\nkind = i_new\n",
            (None, None, None),
            Path(self._tmp.name) / "system.ltx",
        )
        self.assertEqual(
            self.document.current_text,
            "[item]\nkind = i_new\n",
        )
        self.assertIn("kind = i_new", self.view.text())
        self.assertFalse(self.document.is_dirty)

    def test_reopen_dirty_document_keeps_draft(self):
        self.api.apply_resolved_field_edit(
            self.tab_id,
            "item",
            "kind",
            "i_med",
        )
        self.assertTrue(self.document.is_dirty)
        self.api.open_resolved_tab(
            self.tab_id,
            "[Res]system.ltx",
            "[item]\nkind = i_external\n",
            (None, None, None),
            Path(self._tmp.name) / "system.ltx",
        )
        self.assertIn("kind = i_med", self.document.current_text)

    def test_saved_changes_are_not_overwritten_by_reopen(self):
        self.api.apply_resolved_field_edit(
            self.tab_id,
            "item",
            "kind",
            "i_med",
        )
        self.api.save_project_now()
        self.assertFalse(self.document.is_dirty)
        self.assertTrue(self.document.has_changes)

        self.api.open_resolved_tab(
            self.tab_id,
            "[Res]system.ltx",
            "[item]\nkind = i_external\n",
            (None, None, None),
            Path(self._tmp.name) / "system.ltx",
        )
        self.assertIn("kind = i_med", self.document.current_text)

    def test_closing_resolved_tab_keeps_document_for_open_card(self):
        self.feature.open_card(self.tab_id, "item")
        self.window.editor.close_tab(self.tab_id)
        self.assertIsNotNone(self.api.resolved_document(self.tab_id))

        card_tab = self.feature._card_tab_id(self.tab_id, "item")
        self.window.editor.close_tab(card_tab)
        self.assertIsNone(self.api.resolved_document(self.tab_id))


if __name__ == "__main__":
    unittest.main()
