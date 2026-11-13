# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.support import SRC  # noqa: F401

from sdk import ProjectSetting, ProjectSettingChoice
from sdk.events import Events
from ui.platform_api import PlatformApi
from ui.project import ProjectService


class FakeEvents:
    def __init__(self):
        self._subscribers = {}

    def subscribe(self, name, callback):
        self._subscribers.setdefault(name, []).append(callback)
        return None

    def publish(self, name, payload=None):
        for callback in self._subscribers.get(name, ()):
            callback(payload)


class FakeCommands:
    def __init__(self):
        self._handlers = {}

    def register(self, name, handler):
        self._handlers[name] = handler

    def execute(self, name, *args, **kwargs):
        return self._handlers[name](*args, **kwargs)


class FakeWidget:
    def __init__(self):
        self.lines = []

    def goto_line(self, line_number):
        self.lines.append(line_number)


class FakeEditor:
    def __init__(self):
        self.widget = FakeWidget()
        self.tabs = {}
        self.dirty = {}
        self.current = self.widget

    def currentWidget(self):
        return self.current

    def get_tab(self, tab_id):
        return self.tabs.get(tab_id)

    def set_tab_dirty(self, tab_id, dirty):
        self.dirty[tab_id] = bool(dirty)

    def mark_tab_clean(self, tab_id):
        self.dirty[tab_id] = False

    def setCurrentWidget(self, widget):
        self.current = widget


class FakeStatusBar:
    def showMessage(self, *_args, **_kwargs):
        pass


class FakeWindow:
    def __init__(self):
        self.editor = FakeEditor()

    def log(self, _message):
        pass

    def log_status(self, _key, _message):
        pass

    def statusBar(self):
        return FakeStatusBar()

    def setWindowTitle(self, _title):
        pass


class PublishingHandler:
    """Имитация обработчика шины: публикует FILE_OPENED после открытия."""

    def __init__(self, events):
        self._events = events

    def open(self, path, api):
        api.events.publish(Events.FILE_OPENED, path)
        return True


class SilentHandler:
    """Открывает файл, не публикуя событие (для проверки pending-навигации)."""

    def open(self, path, api):
        return True


class PlatformApiTest(unittest.TestCase):
    def test_open_at_line_applies_pending_navigation(self):
        events = FakeEvents()
        commands = FakeCommands()
        api = PlatformApi(events, commands, None, None, None)
        api.window = FakeWindow()
        api.router.set_default(PublishingHandler(events))

        path = Path("a.ltx")
        self.assertTrue(api.request_open_file_at_line(path, 42))

        self.assertEqual(api.window.editor.widget.lines, [42])
        self.assertEqual(api._pending_file_actions, {})

    def test_failed_open_cancels_pending_navigation(self):
        events = FakeEvents()
        commands = FakeCommands()
        api = PlatformApi(events, commands, None, None, None)
        api.window = FakeWindow()

        class FailingHandler:
            def open(self, path, api):
                api.events.publish(Events.FILE_OPEN_FAILED, path)
                return True

        api.router.set_default(FailingHandler())

        path = Path("a.ltx")
        self.assertTrue(api.request_open_file_at_line(path, 42))

        self.assertEqual(api.window.editor.widget.lines, [])
        self.assertEqual(api._pending_file_actions, {})

    def test_navigation_waits_for_matching_path(self):
        events = FakeEvents()
        commands = FakeCommands()
        api = PlatformApi(events, commands, None, None, None)
        api.window = FakeWindow()
        api.router.set_default(SilentHandler())
        path = Path("a.ltx")
        other = Path("b.ltx")

        self.assertTrue(api.request_open_file_at_line(path, 42))
        # Событие о другом файле не должно применить навигацию.
        events.publish(Events.FILE_OPENED, other)
        self.assertEqual(api.window.editor.widget.lines, [])
        self.assertTrue(
            any(
                Path(key).name == path.name
                for key in api._pending_file_actions
            ),
            api._pending_file_actions,
        )

        events.publish(Events.FILE_OPENED, path)
        self.assertEqual(api.window.editor.widget.lines, [42])
        self.assertEqual(api._pending_file_actions, {})

    def test_request_open_without_line_does_not_add_navigation(self):
        events = FakeEvents()
        commands = FakeCommands()
        api = PlatformApi(events, commands, None, None, None)
        api.window = FakeWindow()
        api.router.set_default(PublishingHandler(events))

        path = Path("a.ltx")
        self.assertTrue(api.request_open_file(path))

        self.assertEqual(api._pending_file_actions, {})
        self.assertEqual(api.window.editor.widget.lines, [])


class ProjectSettingPlatformTest(unittest.TestCase):
    def test_register_read_and_persist_module_setting(self):
        with tempfile.TemporaryDirectory() as d:
            paths = SimpleNamespace(
                projects_dir=Path(d) / "projects",
                app_dir=Path(d),
            )
            api = PlatformApi(
                FakeEvents(),
                FakeCommands(),
                None,
                None,
                paths,
            )
            api.project.create("settings", save_mode="root")

            setting = ProjectSetting(
                key="test.mode",
                label="Режим",
                kind="choice",
                default="override",
                choices=(
                    ProjectSettingChoice("override", "Override"),
                    ProjectSettingChoice("merge", "Merge"),
                ),
            )

            self.assertTrue(api.register_project_setting(setting))
            self.assertEqual(
                api.project_setting(setting.key),
                "override",
            )

            self.assertTrue(
                api.set_project_setting(setting.key, "merge")
            )
            self.assertEqual(
                api.project.module_setting(setting.key),
                "merge",
            )
            self.assertEqual(
                api.project_setting(setting.key),
                "merge",
            )

            self.assertFalse(
                api.set_project_setting("unknown.setting", 1)
            )
            self.assertTrue(
                api.unregister_project_setting(setting.key)
            )


class _RestoreResolver:
    def __init__(self):
        self.invalidations = 0

    def invalidate_cache(self):
        self.invalidations += 1


class _RestoreServices:
    def __init__(self):
        self.resolver = _RestoreResolver()

    def add_virtual_file(self, forest, path, text):
        if path in forest.files:
            return False
        forest.files.append(Path(path))
        return True

    def remove_virtual_file(self, forest, path):
        if path not in forest.files:
            return False
        forest.files = [item for item in forest.files if item != path]
        return True


class _RestoreForest:
    def __init__(self):
        self.files = []
        self.xml_files = []


class ProjectVfsRestoreTest(unittest.TestCase):
    def test_loading_project_adds_and_removes_new_files(self):
        with tempfile.TemporaryDirectory() as d:
            events = FakeEvents()
            commands = FakeCommands()
            api = PlatformApi(events, commands, None, None, None)
            api.window = FakeWindow()
            api.services = _RestoreServices()
            api._forest = _RestoreForest()
            api.project = ProjectService(Path(d) / "projects")
            api.project.create("Proj")

            path = (
                Path(d)
                / "gamedata"
                / "configs"
                / "items"
                / "mod_item_base_~ace_p.ltx"
            )
            api.project.save_draft(path, "[item]\n")

            added, removed = api._restore_project_virtual_files()

            self.assertEqual((added, removed), (1, 0))
            self.assertIn(path, api._forest.files)
            self.assertEqual(api.services.resolver.invalidations, 1)

            api.project.remove_draft(path)
            added, removed = api._restore_project_virtual_files()

            self.assertEqual((added, removed), (0, 1))
            self.assertNotIn(path, api._forest.files)
            self.assertEqual(api.services.resolver.invalidations, 2)


class ResolvedDocumentPlatformTest(unittest.TestCase):
    def _api(self, root):
        events = FakeEvents()
        commands = FakeCommands()
        paths = SimpleNamespace(
            projects_dir=Path(root) / "projects",
            app_dir=Path(root),
        )
        api = PlatformApi(events, commands, None, None, paths)
        api.window = FakeWindow()
        api.project.create("documents", save_mode="root")
        api.services = _FakeServices()
        api._forest = object()
        return api, events

    def test_line_edit_updates_document_and_project_draft(self):
        with tempfile.TemporaryDirectory() as d:
            api, _events = self._api(d)
            tab_id = "base.resolved:system.ltx"

            api.ensure_resolved_document(
                tab_id,
                Path(d) / "system.ltx",
                "[Res]system.ltx",
                "[sec]\nx = 1\n",
                (None, None, None),
            )
            self.assertTrue(
                api.apply_resolved_line_edit(
                    tab_id,
                    "replace",
                    2,
                    "x = 9",
                )
            )

            document = api.resolved_document(tab_id)
            self.assertEqual(document.current_text, "[sec]\nx = 9\n")
            self.assertTrue(document.is_dirty)
            self.assertEqual(
                api.project.get_resolved_draft(tab_id).current_text,
                "[sec]\nx = 9\n",
            )

    def test_update_with_line_edit_ignores_stale_full_text(self):
        with tempfile.TemporaryDirectory() as d:
            api, _events = self._api(d)
            tab_id = "base.resolved:system.ltx"

            api.ensure_resolved_document(
                tab_id,
                Path(d) / "system.ltx",
                "[Res]system.ltx",
                "[sec]\nx = 1\n",
                (None, None, None),
            )
            # Карточка присылает устаревший полный текст, но актуальную
            # строчную правку — должна примениться только она.
            from sdk.api import ResolvedLineEdit

            self.assertTrue(
                api.update_resolved_draft(
                    tab_id,
                    Path(d) / "system.ltx",
                    "[Res]system.ltx",
                    "[sec]\nx = 1\n",
                    "[sec]\nx = 999\n",
                    (None, None, None),
                    line_edit=ResolvedLineEdit(
                        action="replace",
                        line_number=2,
                        text="x = 5",
                    ),
                )
            )
            self.assertEqual(
                api.resolved_document(tab_id).current_text,
                "[sec]\nx = 5\n",
            )

    def test_document_change_is_published(self):
        with tempfile.TemporaryDirectory() as d:
            api, events = self._api(d)
            tab_id = "base.resolved:system.ltx"
            api.ensure_resolved_document(
                tab_id,
                Path(d) / "system.ltx",
                "[Res]system.ltx",
                "[sec]\nx = 1\n",
                (None, None, None),
            )
            seen = []
            events.subscribe(
                Events.RESOLVED_DOCUMENT_CHANGED,
                lambda change: seen.append(change),
            )
            api.apply_resolved_line_edit(tab_id, "replace", 2, "x = 7")
            self.assertEqual(len(seen), 1)
            self.assertEqual(seen[0].tab_id, tab_id)
            self.assertIn("sec", seen[0].sections)

    def test_item_entry_projection(self):
        with tempfile.TemporaryDirectory() as d:
            api, _events = self._api(d)
            tab_id = "base.resolved:system.ltx"
            api.ensure_resolved_document(
                tab_id,
                Path(d) / "system.ltx",
                "[Res]system.ltx",
                "[sec]\nkind = i_food\n",
                (None, None, None),
            )
            entry = api.item_entry(tab_id, "sec")
            self.assertIsNotNone(entry)
            self.assertEqual(entry.section, "sec")

    def test_write_project_texts_rolls_back_on_failure(self):
        from unittest.mock import patch

        from ui.project import ProjectError

        with tempfile.TemporaryDirectory() as d:
            api, _events = self._api(d)
            first = Path(d) / "first.ltx"
            second = Path(d) / "second.ltx"
            api.project.save_draft(first, "old")

            original_save = api.project.save_draft
            calls = {"count": 0}

            def failing_save(path, text=None):
                calls["count"] += 1
                if calls["count"] == 2:
                    raise ProjectError("disk full")
                return original_save(path, text)

            with patch.object(
                api.project,
                "save_draft",
                side_effect=failing_save,
            ):
                self.assertFalse(
                    api.write_project_texts(
                        ((first, "new"), (second, "new"))
                    )
                )

            self.assertEqual(api.project.get_text(first), "old")
            self.assertIsNone(api.project.get_text(second))

    def test_ensure_refreshes_clean_document(self):
        with tempfile.TemporaryDirectory() as d:
            api, _events = self._api(d)
            tab_id = "base.resolved:system.ltx"
            api.ensure_resolved_document(
                tab_id,
                Path(d) / "system.ltx",
                "[Res]system.ltx",
                "[a]\nx = 1\n",
                (None, None, None),
            )
            api.ensure_resolved_document(
                tab_id,
                Path(d) / "system.ltx",
                "[Res]system.ltx",
                "[a]\nx = 2\n",
                (None, None, None),
            )
            self.assertEqual(
                api.resolved_document(tab_id).current_text,
                "[a]\nx = 2\n",
            )

    def test_ensure_keeps_dirty_document(self):
        with tempfile.TemporaryDirectory() as d:
            api, _events = self._api(d)
            tab_id = "base.resolved:system.ltx"
            api.ensure_resolved_document(
                tab_id,
                Path(d) / "system.ltx",
                "[Res]system.ltx",
                "[a]\nx = 1\n",
                (None, None, None),
            )
            api.apply_resolved_line_edit(tab_id, "replace", 2, "x = 5")
            api.ensure_resolved_document(
                tab_id,
                Path(d) / "system.ltx",
                "[Res]system.ltx",
                "[a]\nx = 2\n",
                (None, None, None),
            )
            self.assertEqual(
                api.resolved_document(tab_id).current_text,
                "[a]\nx = 5\n",
            )


class _FakeServices:
    def invalidate_resolver_cache(self):
        return None

    def build_item_entry(self, forest, resolved_text, section, log=None):
        from sdk.api import ItemEntry, ItemField

        return ItemEntry(
            section=str(section),
            fields=(ItemField("kind", "i_food", 2),),
        )


if __name__ == "__main__":
    unittest.main()
