# -*- coding: utf-8 -*-
"""Правка resolved-строк system.ltx из карточки предмета."""

import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from tests.qt_support import qapp
from sdk.api import ItemCatalog, ItemEntry, ItemField, ResolvedDraftInfo
from ui.editor import ResolvedView
from modules.base_module.item_viewer import ItemViewerFeature, ItemViewerWindow

_app = qapp()


class _Ui:
    def __init__(self):
        self.text = None
        self.sources = None
        self.opened = None
        self.draft = None
        self.errors = []

    def is_project_active(self):
        return True

    def resolved_draft(self, tab_id):
        return self.draft

    def update_resolved_draft(
        self,
        tab_id,
        root_path,
        title,
        original_text,
        current_text,
        line_sources=(),
        line_edit=None,
    ):
        self.text = current_text
        self.sources = tuple(line_sources)
        return True

    def sync_resolved_tab(self, *args):
        pass

    def open_resolved_tab(self, *args, **kwargs):
        self.opened = (args, kwargs)

    def save_project_now(self):
        return 1

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


class _Session:
    config_dir = None
    forest = None


class ItemLineEditTest(unittest.TestCase):
    def setUp(self):
        self.ctx = _Ctx()
        self.feature = ItemViewerFeature(self.ctx, _Session())
        self.feature._root_path = Path("system.ltx")
        self.feature._tab_id = "base.resolved:system.ltx"
        self.feature._title = "RESOLVED: system.ltx"
        self.feature._resolved_text = (
            "[item]\nkind = i_food\ncost = 10\n"
        )
        self.feature._original_text = self.feature._resolved_text
        self.feature._line_sources = (None, None, None)

    def test_replace_line(self):
        self.assertTrue(
            self.feature._replace_line(2, "kind = i_med")
        )
        self.assertIn("kind = i_med", self.ctx.ui.text)
        self.assertNotIn("kind = i_food", self.ctx.ui.text)

    def test_insert_after_line(self):
        self.assertTrue(
            self.feature._insert_line(2, "weight = 1")
        )
        lines = self.ctx.ui.text.split("\n")
        self.assertEqual(lines[2], "weight = 1")
        self.assertEqual(len(self.ctx.ui.sources), len(lines))

    def test_delete_line(self):
        self.assertTrue(self.feature._delete_line(2))
        self.assertNotIn("kind = i_food", self.ctx.ui.text)
        self.assertEqual(
            len(self.ctx.ui.sources),
            len(self.ctx.ui.text.split("\n")),
        )

    def test_external_draft_change_is_not_overwritten(self):
        self.ctx.ui.draft = ResolvedDraftInfo(
            text="[item]\nkind = changed\n",
            saved_text="[item]\nkind = changed\n",
            original_text=self.feature._original_text,
        )
        self.assertFalse(
            self.feature._replace_line(2, "kind = i_med")
        )
        self.assertTrue(self.ctx.ui.errors)


class ItemViewerLocalEditTest(unittest.TestCase):
    def test_local_replace_delete_insert(self):
        window = ItemViewerWindow(
            can_edit=lambda: True,
            on_replace_line=lambda line, text: True,
            on_delete_line=lambda line: True,
            on_insert_line=lambda line, text: True,
            on_go_to_line=lambda line: None,
        )
        entry = ItemEntry(
            section="item",
            fields=(
                ItemField("kind", "i_food", 2),
                ItemField("cost", "10", 3),
            ),
            icon_texture=r"ui\x",
            name_text="Item",
            line_number=1,
        )
        window.set_catalog(ItemCatalog(items=(entry,)), {})

        window._apply_local_replace(
            window._current_entry,
            1,
            "cost",
            "20",
        )
        self.assertEqual(
            window._current_entry.fields[1].value,
            "20",
        )

        window._apply_local_insert(
            window._current_entry,
            window._current_entry.fields[1],
            3,
            "weight = 1",
        )
        self.assertEqual(
            [(f.key, f.line_number) for f in window._current_entry.fields],
            [("kind", 2), ("cost", 3), ("weight", 4)],
        )

        window._apply_local_delete(
            window._current_entry,
            1,
            window._current_entry.fields[1],
        )
        self.assertEqual(
            [(f.key, f.line_number) for f in window._current_entry.fields],
            [("kind", 2), ("weight", 3)],
        )
        window.close()


class ResolvedLineEditTest(unittest.TestCase):
    def test_replace_insert_delete_are_local(self):
        view = ResolvedView()
        view.set_content(
            "[item]\nkind = i_food\ncost = 10\n",
            (None, None, None, None),
            "system.ltx",
        )

        self.assertTrue(
            view.apply_line_edit("replace", 2, "kind = i_med")
        )
        self.assertIn("kind = i_med", view.text())
        self.assertNotIn("kind = i_food", view.text())

        self.assertTrue(
            view.apply_line_edit("insert", 2, "weight = 1")
        )
        self.assertEqual(
            view.text().split("\n")[2],
            "weight = 1",
        )

        self.assertTrue(view.apply_line_edit("delete", 2))
        self.assertNotIn("kind = i_med", view.text())
        self.assertEqual(
            len(view.line_sources),
            len(view.text().split("\n")),
        )
        view.close()


if __name__ == "__main__":
    unittest.main()
