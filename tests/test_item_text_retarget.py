# -*- coding: utf-8 -*-
"""Текст предмета: проектные ключи и синхронизация с system.ltx."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from tests.support import SRC  # noqa: F401

from tests.qt_support import qapp
from sdk.contracts import ProjectOverlay
from sdk.features.items import ItemEntry, ItemField, LocalizationString
from app.features.items.catalog import ItemCatalogService
from app.features.items.creation import creation_paths, plan_item_creation
from core.cache.service import CacheService
from core.localization import LocalizationService, edit_localization_text
from ui.platform.sdk_bridge import ItemCatalogApiAdapter
from modules.item_browser.item_viewer import ItemViewerFeature

_app = qapp()


class _Ui:
    def __init__(self, project_name="proj", new_files=()):
        self._project_name = project_name
        self._new_files = frozenset(Path(path) for path in new_files)
        self.errors = []
        self.logs = []
        self.status = []
        self.busy = []

    def is_project_active(self):
        return True

    def project_name(self):
        return self._project_name

    def project_overlay(self):
        return ProjectOverlay(new_files=self._new_files)

    def show_error(self, message):
        self.errors.append(str(message))

    def log(self, message):
        self.logs.append(str(message))

    def set_status(self, message, timeout=0):
        self.status.append(str(message))

    def set_editor_tab_busy(self, tab_id, busy):
        self.busy.append((str(tab_id), bool(busy)))


class _Documents:
    def __init__(self, defer_async=False):
        self.files = {}
        self.async_calls = []
        self.pending = []
        self.field_edits = []
        self.defer_async = bool(defer_async)

    def has_resolved_document(self, tab_id):
        return True

    def resolved_document_title(self, tab_id):
        return ""

    def read_project_text(self, path):
        return self.files.get(Path(path), "")

    def write_project_text(self, path, text):
        self.files[Path(path)] = str(text)
        return True

    def write_project_text_async(
        self,
        path,
        text,
        on_done=None,
        original_text=None,
    ):
        self.async_calls.append((Path(path), str(text), original_text))
        if self.defer_async:
            self.pending.append((Path(path), str(text), on_done))
            return True
        ok = self.write_project_text(path, text)
        if on_done is not None:
            on_done(ok, None if ok else "write failed")
        return ok

    def apply_resolved_field_edit_async(
        self,
        tab_id,
        section,
        key,
        value,
        on_done=None,
        owner=None,
    ):
        self.field_edits.append((tab_id, section, key, value))
        if on_done is not None:
            on_done(True, None)
        return True

    def apply_resolved_field_edit(self, tab_id, section, key, value):
        self.field_edits.append((tab_id, section, key, value))
        return True


class _ItemsProvider:
    def __init__(self, entry):
        self.entry = entry

    def item_entry(self, tab_id, section):
        return self.entry

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

    def creation_paths(self, scan_dir, project_name):
        return creation_paths(scan_dir, project_name)

    def edit_localization_text(self, text, key, value):
        return edit_localization_text(text, key, value)


class _Services:
    def __init__(self, config_dir):
        self.config_dir = Path(config_dir)

    def forest_scan_dir(self, forest):
        return self.config_dir


class _Forest:
    def __init__(self, config_dir):
        self.scan_dir = str(config_dir)
        self.files = []
        self.xml_files = []


class _Resolver:
    def assemble_resolved(self, root_path, forest, config_dir, log=None):
        return "", []


class _Ctx:
    def __init__(self, ui, documents, entry, config_dir):
        self.ui = ui
        self.documents = documents
        self.services = _Services(config_dir)
        self.items = ItemCatalogApiAdapter(
            _ItemsProvider(entry),
            SimpleNamespace(
                item_entry=lambda tab_id, section: entry,
            ),
        )
        self.paths = SimpleNamespace(
            key=lambda path: str(Path(path)).lower(),
            same=lambda left, right: (
                str(Path(left)).lower() == str(Path(right)).lower()
            ),
        )
        self.projects = ui

    def item_entry(self, tab_id, section):
        return self.items.item_entry(tab_id, section)


SOURCE_XML = """<?xml version="1.0" encoding="windows-1251"?>
<string_table>
\t<string id="st_name"><text>Старое имя</text></string>
\t<string id="st_desc"><text>Старое описание</text></string>
</string_table>
"""


class ItemTextRetargetTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.configs = Path(self.temp.name) / "gamedata" / "configs"
        self.source = self.configs / "text" / "rus" / "st_items.xml"
        self.source.parent.mkdir(parents=True)
        self.source.write_text(SOURCE_XML, encoding="utf-8")

    def _feature(
        self,
        entry,
        documents=None,
        project_name="123123123",
        defer_async=False,
        owned=(),
    ):
        documents = documents or _Documents(defer_async=defer_async)
        ui = _Ui(project_name=project_name, new_files=owned)
        feature = ItemViewerFeature(
            _Ctx(ui, documents, entry, self.configs),
            session=SimpleNamespace(forest=object()),
        )
        self.addCleanup(feature.close)
        return feature, ui, documents

    def _entry(self, source=None, short_key="st_name"):
        source = Path(source or self.source)
        return ItemEntry(
            section="beans",
            fields=(
                ItemField("inv_name", "st_name", 2),
                ItemField("inv_name_short", short_key, 3),
                ItemField("description", "st_desc", 4),
            ),
            name_text="Старое имя",
            description_text="Старое описание",
            name_key="st_name",
            description_key="st_desc",
            name_source=LocalizationString(
                "st_name",
                "Старое имя",
                source,
            ),
            description_source=LocalizationString(
                "st_desc",
                "Старое описание",
                source,
            ),
        )

    def test_external_key_gets_project_pair_and_ltx_fields(self):
        feature, ui, documents = self._feature(self._entry())

        self.assertTrue(
            feature._apply_item_text(
                "tab",
                "beans",
                "description",
                "Новое описание",
            )
        )

        project_xml = (
            self.configs / "text" / "rus" / "st_ace_123123123.xml"
        )
        self.assertEqual(
            [call[0] for call in documents.async_calls],
            [project_xml],
        )
        text = documents.files[project_xml]
        self.assertIn(
            '<string id="st_ace_123123123_name">',
            text,
        )
        self.assertIn("<text>Старое имя</text>", text)
        self.assertIn(
            '<string id="st_ace_123123123_descr">',
            text,
        )
        self.assertIn("<text>Новое описание</text>", text)
        self.assertEqual(
            documents.field_edits,
            [
                (
                    "tab",
                    "beans",
                    "description",
                    "st_ace_123123123_descr",
                ),
            ],
        )
        self.assertEqual(ui.errors, [])

    def test_name_edit_keeps_different_short_key(self):
        feature, ui, documents = self._feature(
            self._entry(short_key="st_short")
        )

        self.assertTrue(
            feature._apply_item_text("tab", "beans", "name", "Новое имя")
        )

        keys = [edit[2] for edit in documents.field_edits]
        self.assertIn("inv_name", keys)
        self.assertNotIn("inv_name_short", keys)
        self.assertNotIn("description", keys)
        self.assertEqual(ui.errors, [])

    def test_project_xml_keys_are_visible_through_overlay(self):
        feature, ui, documents = self._feature(self._entry())

        self.assertTrue(
            feature._apply_item_text(
                "tab",
                "beans",
                "description",
                "Новое описание",
            )
        )

        project_xml = (
            self.configs / "text" / "rus" / "st_ace_123123123.xml"
        )
        overlay = ProjectOverlay(
            source_files={project_xml: documents.files[project_xml]},
            new_files=frozenset({project_xml}),
        )
        table = LocalizationService().load(
            config_dir=self.configs,
            overlay=overlay,
        )

        self.assertEqual(
            table.translate("st_ace_123123123_name"),
            "Старое имя",
        )
        self.assertEqual(
            table.translate("st_ace_123123123_descr"),
            "Новое описание",
        )
        self.assertEqual(ui.errors, [])

    def test_catalog_entry_resolves_project_key(self):
        feature, ui, documents = self._feature(self._entry())

        self.assertTrue(
            feature._apply_item_text(
                "tab",
                "beans",
                "description",
                "Новое описание",
            )
        )

        project_xml = (
            self.configs / "text" / "rus" / "st_ace_123123123.xml"
        )
        overlay = ProjectOverlay(
            source_files={project_xml: documents.files[project_xml]},
            new_files=frozenset({project_xml}),
        )
        cache = CacheService()
        catalog = ItemCatalogService(
            _Resolver(),
            localization_service=LocalizationService(
                cache_service=cache,
            ),
            cache_service=cache,
        )
        entry = catalog.build_entry(
            _Forest(self.configs),
            (
                "[beans]\n"
                "inv_name = st_ace_123123123_name\n"
                "description = st_ace_123123123_descr\n"
            ),
            "beans",
            overlay=overlay,
        )

        self.assertIsNotNone(entry)
        self.assertEqual(entry.description_text, "Новое описание")
        self.assertEqual(
            entry.description_key,
            "st_ace_123123123_descr",
        )
        self.assertEqual(entry.description_source.path, project_xml)
        self.assertEqual(ui.errors, [])

    def test_name_edit_moves_shared_short_key(self):
        feature, ui, documents = self._feature(self._entry())

        self.assertTrue(
            feature._apply_item_text("tab", "beans", "name", "Новое имя")
        )

        self.assertEqual(
            documents.field_edits,
            [
                ("tab", "beans", "inv_name", "st_ace_123123123_name"),
                (
                    "tab",
                    "beans",
                    "inv_name_short",
                    "st_ace_123123123_name",
                ),
            ],
        )
        self.assertEqual(ui.errors, [])

    def test_include_fragment_gets_file_in_language_root(self):
        fragment = (
            self.configs / "text" / "rus" / "parts" / "items.xml"
        )
        fragment.parent.mkdir(parents=True)
        fragment.write_text(SOURCE_XML, encoding="utf-8")
        feature, ui, documents = self._feature(
            self._entry(source=fragment)
        )

        self.assertTrue(
            feature._apply_item_text(
                "tab",
                "beans",
                "description",
                "Новое описание",
            )
        )

        project_xml = (
            self.configs / "text" / "rus" / "st_ace_123123123.xml"
        )
        self.assertEqual(
            [call[0] for call in documents.async_calls],
            [project_xml],
        )
        self.assertEqual(ui.errors, [])

    def test_project_owned_xml_is_edited_in_place(self):
        project_xml = (
            self.configs / "text" / "rus" / "st_ace_123123123.xml"
        )
        own_xml = (
            '<string_table>\n'
            '\t<string id="st_ace_123123123_name">'
            "<text>Имя</text></string>\n"
            '\t<string id="st_ace_123123123_descr">'
            "<text>Старое</text></string>\n"
            "</string_table>\n"
        )
        documents = _Documents()
        documents.files[project_xml] = own_xml
        # Файл уже мог попасть в VFS после применения патча: проектный
        # overlay всё равно должен считаться владельцем ключа.
        project_xml.write_text(own_xml, encoding="utf-8")
        entry = self._entry(source=project_xml)
        feature, ui, documents = self._feature(
            entry,
            documents=documents,
            owned=(project_xml,),
        )

        self.assertTrue(
            feature._apply_item_text(
                "tab",
                "beans",
                "description",
                "Новое",
            )
        )

        self.assertEqual(
            [call[0] for call in documents.async_calls],
            [project_xml],
        )
        self.assertIn("<text>Новое</text>", documents.files[project_xml])
        self.assertEqual(documents.field_edits, [])
        self.assertEqual(ui.errors, [])

    def test_ltx_fields_are_updated_only_after_xml_is_written(self):
        feature, ui, documents = self._feature(
            self._entry(),
            defer_async=True,
        )

        self.assertTrue(
            feature._apply_item_text(
                "tab",
                "beans",
                "description",
                "Новое описание",
            )
        )

        self.assertEqual(len(documents.async_calls), 1)
        self.assertEqual(documents.field_edits, [])

        _path, _text, on_done = documents.pending[0]
        on_done(True, None)

        self.assertEqual(len(documents.field_edits), 1)
        self.assertEqual(ui.errors, [])


if __name__ == "__main__":
    unittest.main()
