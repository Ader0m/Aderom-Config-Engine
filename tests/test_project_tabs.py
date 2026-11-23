# -*- coding: utf-8 -*-
"""Сохранение и восстановление открытых вкладок проекта."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.support import SRC  # noqa: F401

from PyQt5.QtWidgets import QMainWindow, QWidget

from app.projects import ProjectService
from tests.qt_support import qapp
from ui.bus import EventBus
from ui.editor_tabs import EditorTabs, MissingOwnerTab
from ui.platform_api import PlatformApi


class _Services:
    def invalidate_paths(self, _roots):
        pass

    def invalidate_resolver_cache(self):
        pass

    def read_text(self, path):
        from core.tools.fs import read_text_safe

        return read_text_safe(path)

    def read_xml_text(self, path):
        from core.tools.fs import read_xml_text

        return read_xml_text(path)


class _Window(QMainWindow):
    def __init__(self, events):
        super().__init__()
        self.editor = EditorTabs(events=events)

    def log(self, _message):
        pass

    def log_status(self, _key, _message):
        pass


_ALIVE = []


class ProjectTabsStorageTest(unittest.TestCase):
    def test_tabs_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            service = ProjectService(Path(d) / "projects")
            project = service.create("tabs")

            service.save_tabs(
                [
                    {
                        "tab_id": "ui.editor",
                        "title": "a.ltx",
                        "kind": "editor",
                        "source_path": str(Path(d) / "a.ltx"),
                        "active": False,
                    },
                    {
                        "tab_id": "ogg_editor.abc",
                        "title": "sound.ogg",
                        "kind": "module",
                        "source_path": None,
                        "state": {"custom": 1},
                        "active": True,
                    },
                    {"title": "без id"},
                ]
            )

            data = json.loads(
                (project.root_dir / "tabs.json").read_text(encoding="utf-8")
            )
            self.assertEqual(data["version"], service.TABS_VERSION)
            self.assertEqual(len(data["tabs"]), 2)

            loaded = service.load_tabs()
            self.assertEqual(
                [record["tab_id"] for record in loaded],
                ["ui.editor", "ogg_editor.abc"],
            )
            self.assertTrue(loaded[1]["active"])
            self.assertEqual(loaded[1]["state"], {"custom": 1})

            project_id = project.id
            service.close()
            service.load(project_id)
            self.assertEqual(len(service.load_tabs()), 2)

    def test_empty_tabs_are_remembered(self):
        with tempfile.TemporaryDirectory() as d:
            service = ProjectService(Path(d) / "projects")
            project = service.create("empty")
            service.save_tabs([])
            self.assertEqual(service.load_tabs(), [])
            self.assertTrue((project.root_dir / "tabs.json").is_file())


class EditorTabsSessionTest(unittest.TestCase):
    def setUp(self):
        self.app = qapp()

    def test_session_records_kind_order_and_active_tab(self):
        tabs = EditorTabs()
        self.addCleanup(tabs.deleteLater)
        tabs.open_file("a.ltx", text="[a]\n")
        tabs.open_tab("plugin.tab", "Plugin", QWidget())
        tabs.set_tab_dirty("plugin.tab", True)
        tabs.set_current_tab("plugin.tab")

        records = tabs.session_tabs()
        self.assertEqual(
            [record["tab_id"] for record in records],
            ["ui.editor", "plugin.tab"],
        )
        self.assertEqual(records[0]["kind"], "editor")
        self.assertEqual(records[1]["kind"], "module")
        self.assertEqual(records[1]["title"], "Plugin")
        self.assertTrue(records[1]["active"])
        self.assertFalse(records[0]["active"])

    def test_session_state_keeps_readonly_and_notice(self):
        tabs = EditorTabs()
        self.addCleanup(tabs.deleteLater)
        tabs.show_text(
            "[a]\n",
            "Сырой файл",
            readonly=True,
            source_path="a.ltx",
            notice="без резолва",
        )

        record = tabs.session_tabs()[0]
        self.assertEqual(record["kind"], "editor")
        self.assertTrue(record["state"]["readonly"])
        self.assertEqual(record["state"]["notice"], "без резолва")

    def test_missing_owner_tab_shows_message(self):
        tabs = EditorTabs()
        self.addCleanup(tabs.deleteLater)
        widget = tabs.open_missing_owner_tab("plugin.tab", "Plugin")

        self.assertIsInstance(widget, MissingOwnerTab)
        self.assertEqual(
            widget.label.text(),
            "Не обнаружен плагин-владелец вкладки",
        )


class RestoreProjectTabsTest(unittest.TestCase):
    def setUp(self):
        self.app = qapp()

    def _api(self, root):
        events = EventBus()
        api = PlatformApi(
            events,
            SimpleNamespace(register=lambda *a, **k: None),
            None,
            _Services(),
            SimpleNamespace(
                projects_dir=Path(root) / "projects",
                app_dir=Path(root),
            ),
        )
        window = _Window(events)
        api.window = window
        project = api.project.create("restore", save_mode="root")
        _ALIVE.append((api, window))
        return api, window, project

    def test_file_and_plugin_tabs_are_restored(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "a.ltx"
            source.write_text("[a]\nx = 1\n", encoding="windows-1251")

            api, window, _project = self._api(d)
            api.project.update_draft(source, "[a]\nx = 2\n")
            window.editor.open_file(source, text="[a]\nx = 2\n")
            window.editor.open_tab("ogg_editor.abc", "sound.ogg", QWidget())
            window.editor.set_current_tab("ogg_editor.abc")

            self.assertTrue(api._save_open_tabs())
            window.editor.close_all_tabs()
            api._restore_editor_tabs()

            file_tab = window.editor.get_tab("ui.editor")
            self.assertIsNotNone(file_tab)
            self.assertIn("x = 2", file_tab.text())

            plugin_tab = window.editor.get_tab("ogg_editor.abc")
            self.assertIsInstance(plugin_tab, MissingOwnerTab)
            self.assertEqual(
                plugin_tab.label.text(),
                "Не обнаружен плагин-владелец вкладки",
            )
            self.assertEqual(
                window.editor.current_tab_id,
                "ogg_editor.abc",
            )

    def test_named_raw_tab_is_restored_in_its_own_tab(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "mod_items_patch.ltx"
            source.write_text("[a]\nx = 1\n", encoding="windows-1251")

            api, window, _project = self._api(d)
            tab_id = "base.raw:mod_items_patch.ltx"
            window.editor.show_text(
                "[a]\nx = 1\n",
                "DLTX PATCH: mod_items_patch.ltx",
                readonly=False,
                source_path=source,
                notice="DLTX-ПАТЧ",
                tab_id=tab_id,
            )

            self.assertTrue(api._save_open_tabs())
            window.editor.close_all_tabs()
            api._restore_editor_tabs()

            restored = window.editor.get_tab(tab_id)
            self.assertIsNotNone(restored)
            self.assertIn("x = 1", restored.text())
            self.assertEqual(window.editor.current_tab_id, tab_id)

    def test_project_activation_replaces_tabs_from_session(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "a.ltx"
            source.write_text("[a]\nx = 1\n", encoding="windows-1251")

            api, window, project = self._api(d)
            window.editor.open_file(source, text="[a]\nx = 1\n")
            window.editor.open_tab("ogg_editor.abc", "sound.ogg", QWidget())
            self.assertTrue(api._save_open_tabs())

            project_id = project.id
            api._begin_project_activation()
            try:
                loaded = api.project.load(project_id)
                api._activate_project_state(loaded, "Проект загружен")
            finally:
                api._end_project_activation()

            self.assertIsNotNone(window.editor.get_tab("ui.editor"))
            self.assertIsInstance(
                window.editor.get_tab("ogg_editor.abc"),
                MissingOwnerTab,
            )


if __name__ == "__main__":
    unittest.main()
