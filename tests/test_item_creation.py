# -*- coding: utf-8 -*-
"""Создание нового предмета из окна просмотра."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from PyQt5.QtCore import Qt

from tests.support import SRC  # noqa: F401

from tests.qt_support import qapp
from sdk.api import (
    ItemCatalog,
    ItemEntry,
    ItemField,
    ItemSection,
    ProjectOverlay,
)
from modules.base_module.item_viewer import (
    CreateItemDialog,
    ItemViewerFeature,
    ItemViewerWindow,
)

_app = qapp()


class _Ui:
    def __init__(self):
        self.files = {}
        self.errors = []
        self.logs = []
        self.overlay = ProjectOverlay()
        self.resolved = None

    def is_project_active(self):
        return True

    def resolved_draft(self, tab_id):
        return self.resolved

    def project_name(self):
        return "Мой проект"

    def project_overlay(self):
        return self.overlay

    def read_project_text(self, path):
        return self.files.get(Path(path), "")

    def write_project_text(self, path, text):
        self.files[Path(path)] = str(text)
        return True

    def write_project_texts(self, files):
        for path, text in files:
            self.files[Path(path)] = str(text)
        return True

    def show_error(self, message):
        self.errors.append(str(message))

    def set_status(self, *args, **kwargs):
        pass

    def log(self, message):
        self.logs.append(str(message))

    def theme_color(self, name, fallback=None):
        return fallback


class _Services:
    def __init__(self, scan_dir):
        self._scan_dir = Path(scan_dir)

    def forest_scan_dir(self, forest):
        return self._scan_dir

    def ltx_section_at_line(self, text, line_number):
        from core import parse_ltx_document

        return parse_ltx_document(text).section_at_line(line_number)

    def ltx_section_bounds(self, text, section):
        from core import parse_ltx_document

        return parse_ltx_document(text).section_bounds(section)

    def ltx_key_from_line(self, line):
        from core.patching.ltx.document import parse_key_line
        from core.tools.fs import strip_comments

        parsed = parse_key_line(strip_comments(str(line or "")).strip())
        return parsed[1].lower() if parsed else ""

    def ltx_edit_section(
        self,
        text,
        section,
        key,
        action,
        new_line=None,
        after_key=None,
    ):
        from core import edit_section_text

        return edit_section_text(
            text,
            section,
            key,
            action,
            new_line=new_line,
            after_key=after_key,
        )

    def ltx_declared_sections(self, text):
        from core import declared_sections

        return declared_sections(text)


class _Ctx:
    def __init__(self, scan_dir):
        self.ui = _Ui()
        self.services = _Services(scan_dir)


class _Forest:
    def __init__(self, scan_dir):
        self.scan_dir = str(scan_dir)


class _Session:
    def __init__(self, forest):
        self.forest = forest
        self.config_dir = None


class CreateItemDialogTest(unittest.TestCase):
    def test_parent_candidates_and_no_parent(self):
        dialog = CreateItemDialog(
            ("base_item", "wpn_base", "base_item"),
        )
        dialog.section_edit.setText("new_item")
        dialog.parent_combo.setCurrentText("wpn_base")
        self.assertEqual(
            dialog.values(),
            ("new_item", "wpn_base"),
        )
        self.assertEqual(dialog.parent_combo.count(), 2)

        dialog.no_parent.setChecked(True)
        self.assertFalse(dialog.parent_combo.isEnabled())
        self.assertEqual(dialog.values(), ("new_item", None))
        dialog.close()

    def test_suggestions_are_filtered_and_choose_parent(self):
        dialog = CreateItemDialog(
            ("wpn_ak74", "wpn_ak74_base", "item_base", "af_base"),
        )
        dialog.parent_combo.setCurrentText("ak74")
        dialog._show_suggestions("ak74")

        values = [
            dialog._suggestion_popup.list.item(i).text()
            for i in range(dialog._suggestion_popup.list.count())
        ]
        self.assertEqual(values, ["wpn_ak74", "wpn_ak74_base"])

        dialog._on_suggestion_chosen("wpn_ak74_base")
        self.assertEqual(
            dialog.parent_combo.currentText(),
            "wpn_ak74_base",
        )
        self.assertFalse(dialog._suggestion_popup.isVisible())
        dialog.close()

    def test_empty_query_lists_all_parents_for_scrolling(self):
        dialog = CreateItemDialog(
            ["parent_%02d" % index for index in range(30)]
        )
        dialog._show_suggestions("")
        self.assertEqual(
            dialog._suggestion_popup.list.count(),
            30,
        )
        self.assertEqual(
            dialog._suggestion_popup.list.verticalScrollBarPolicy(),
            Qt.ScrollBarAsNeeded,
        )
        dialog.close()


class ParentCandidatesTest(unittest.TestCase):
    def test_uses_parents_from_raw_files(self):
        window = ItemViewerWindow()
        window.set_catalog(
            ItemCatalog(
                parent_names=("base_item", "wpn_base"),
                sections=(
                    ItemSection(name="item"),
                    ItemSection(name="base_item"),
                ),
            ),
            {},
        )
        self.assertEqual(
            window._parent_candidates(),
            {"base_item", "wpn_base"},
        )
        window.close()

    def test_falls_back_to_all_section_names(self):
        window = ItemViewerWindow()
        window.set_catalog(
            ItemCatalog(
                sections=(
                    ItemSection(name="one"),
                    ItemSection(name="two"),
                ),
            ),
            {},
        )
        self.assertEqual(
            window._parent_candidates(),
            {"one", "two"},
        )
        window.close()


class NewItemFileEditTest(unittest.TestCase):
    def test_field_edit_updates_new_file_not_resolved_draft(self):
        with TemporaryDirectory() as d:
            cfg = Path(d) / "gamedata" / "configs"
            cfg.mkdir(parents=True)
            path = cfg / "items" / "mod_item_base_~ace_p.ltx"
            file_text = (
                "[new_item]:base_item\n"
                "kind =\n"
                "cost =\n"
            )

            ctx = _Ctx(cfg)
            ctx.ui.files[path] = file_text
            ctx.ui.overlay = ProjectOverlay(
                source_files={path: file_text},
                new_files=frozenset({path}),
            )
            feature = ItemViewerFeature(
                ctx,
                _Session(_Forest(cfg)),
            )
            feature._root_path = Path("system.ltx")
            feature._tab_id = "base.resolved:system.ltx"
            feature._title = "[Res]system.ltx"
            ctx.ui.resolved = SimpleNamespace(
                text=(
                    "[base_item]\n"
                    "kind = i_food\n"
                    "\n"
                    "[new_item]\n"
                    "kind =\n"
                    "cost =\n"
                ),
                line_sources=(
                    None,
                    None,
                    None,
                    SimpleNamespace(path=path),
                    SimpleNamespace(path=path),
                    SimpleNamespace(path=path),
                ),
            )
            feature.show = lambda: None

            self.assertTrue(
                feature._replace_line(
                    feature._tab_id,
                    5,
                    "kind = i_med",
                )
            )

            self.assertIn("kind = i_med", ctx.ui.files[path])
            self.assertEqual(
                ctx.ui.resolved.text,
                "[base_item]\nkind = i_food\n\n"
                "[new_item]\nkind =\ncost =\n",
            )


class ItemCreationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.scan_dir = Path(self._tmp.name) / "gamedata" / "configs"
        self.scan_dir.mkdir(parents=True)

        self.ctx = _Ctx(self.scan_dir)
        self.feature = ItemViewerFeature(
            self.ctx,
            _Session(_Forest(self.scan_dir)),
        )
        self.feature._root_path = Path("system.ltx")
        self.feature._tab_id = "base.resolved:system.ltx"
        self.feature._title = "[Res]system.ltx"
        self.ctx.ui.resolved = SimpleNamespace(
            text="[base]\n",
            line_sources=(),
        )
        self.feature._catalog = ItemCatalog(
            items=(
                ItemEntry(
                    section="base_item",
                    fields=(
                        ItemField("kind", "i_food"),
                        ItemField("cost", "10"),
                        ItemField("inv_name", "base_name"),
                    ),
                ),
            ),
            sections=(
                ItemSection(
                    name="base_item",
                    fields=("kind", "cost", "inv_name"),
                ),
                ItemSection(
                    name="child_item",
                    fields=("kind",),
                    parents=("base_item",),
                ),
            ),
        )

        self.item_path = (
            self.scan_dir
            / "items"
            / "mod_item_base_~ace_мой_проект.ltx"
        )
        self.xml_path = (
            self.scan_dir / "text" / "rus" / "st_ace_мой_проект.xml"
        )

    def test_creates_item_and_localization(self):
        self.assertTrue(self.feature._create_item("new_item", "base_item"))

        item_text = self.ctx.ui.files[self.item_path]
        self.assertIn("[new_item]:base_item", item_text)
        self.assertIn("kind =", item_text)
        self.assertIn("cost =", item_text)
        self.assertIn("inv_name = st_ace_мой_проект_name", item_text)
        self.assertIn(
            "inv_name_short = st_ace_мой_проект_name",
            item_text,
        )
        self.assertIn(
            "description = st_ace_мой_проект_descr",
            item_text,
        )

        xml_text = self.ctx.ui.files[self.xml_path]
        self.assertIn('encoding="windows-1251"', xml_text)
        self.assertIn('id="st_ace_мой_проект_name"', xml_text)
        self.assertIn('id="st_ace_мой_проект_descr"', xml_text)

    def test_second_item_gets_numeric_id(self):
        self.assertTrue(self.feature._create_item("first", "base_item"))
        self.assertTrue(self.feature._create_item("second", None))

        item_text = self.ctx.ui.files[self.item_path]
        self.assertIn("[second]", item_text)
        self.assertIn(
            "inv_name = st_ace_мой_проект_2_name",
            item_text,
        )

        xml_text = self.ctx.ui.files[self.xml_path]
        self.assertIn('id="st_ace_мой_проект_2_name"', xml_text)
        self.assertIn('id="st_ace_мой_проект_2_descr"', xml_text)

    def test_duplicate_section_is_rejected(self):
        self.assertFalse(self.feature._create_item("base_item", None))
        self.assertTrue(self.ctx.ui.errors)
        self.assertFalse(self.item_path in self.ctx.ui.files)


if __name__ == "__main__":
    unittest.main()
