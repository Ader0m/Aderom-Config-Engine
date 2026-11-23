# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.support import SRC  # noqa: F401

from sdk.contracts import FileOpened, FileOpenFailed, TreeReady
from sdk import ForestRef
from sdk.features.projects import ProjectSetting, ProjectSettingChoice
from sdk.contracts.events import Events
from ui.bus import EventBus
from ui.platform_api import PlatformApi
from app.projects import PreviewItem, ProjectService


class FakeEvents:
    def __init__(self):
        self._subscribers = {}

    def subscribe(self, name, callback):
        self._subscribers.setdefault(name, []).append(callback)
        return None

    def publish(self, name, payload=None):
        for callback in self._subscribers.get(name, ()):
            callback(payload)

    def dispatch(self, name, payload=None):
        return False


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
        self.tabs = {"ui.editor": self.widget}
        self.dirty = {}
        self.current = self.widget
        self.busy = False
        self.tab_busy = set()
        self.calls = []
        self.clean_paths = []

    def currentWidget(self):
        return self.current

    def get_tab(self, tab_id):
        return self.tabs.get(tab_id)

    def set_tab_dirty(self, tab_id, dirty):
        self.dirty[tab_id] = bool(dirty)

    def mark_tab_clean(self, tab_id):
        self.dirty[tab_id] = False

    def mark_clean(self, path):
        self.clean_paths.append(Path(path))

    def setCurrentWidget(self, widget):
        self.current = widget

    def set_current_tab(self, tab_id):
        widget = self.tabs.get(str(tab_id))
        if widget is None:
            return False
        self.current = widget
        return True

    def set_editable(self, editable):
        self.calls.append(("editable", bool(editable)))

    def set_busy(self, busy):
        self.busy = bool(busy)
        self.calls.append(("busy", bool(busy)))

    def set_tab_busy(self, tab_id, busy):
        if busy:
            self.tab_busy.add(str(tab_id))
        else:
            self.tab_busy.discard(str(tab_id))

    def open_paths(self):
        return []

    def resolved_tabs(self):
        return []

    def clear_tab_busy(self):
        self.tab_busy.clear()


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
        api.events.publish(
            Events.FILE_OPENED,
            FileOpened(path=Path(path), tab_id="ui.editor"),
        )
        return True


class SilentHandler:
    """Открывает файл, не публикуя событие (для проверки pending-навигации)."""

    def open(self, path, api):
        return True


class PlatformApiTest(unittest.TestCase):
    def test_file_opened_uses_tab_id_not_current_widget(self):
        events = FakeEvents()
        commands = FakeCommands()
        api = PlatformApi(events, commands, None, None, None)
        window = FakeWindow()
        target = FakeWidget()
        other = FakeWidget()
        window.editor.tabs["a"] = target
        window.editor.current = other
        api.window = window

        path = Path("a.ltx")
        api._queue_file_action(path, "line", 77)
        events.publish(
            Events.FILE_OPENED,
            FileOpened(path=path, tab_id="a"),
        )

        self.assertEqual(target.lines, [77])
        self.assertEqual(other.lines, [])

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

    def test_open_raw_at_line_bypasses_module_handlers(self):
        events = FakeEvents()
        commands = FakeCommands()
        api = PlatformApi(events, commands, None, None, None)
        api.window = FakeWindow()
        api.router.set_default(PublishingHandler(events))

        class ResolvingHandler:
            priority = 100

            def can_open(self, path):
                return True

            def open(self, path, api):
                raise AssertionError("module handler must be bypassed")

        api.router.register(ResolvingHandler())

        path = Path("a.xml")
        self.assertTrue(api.request_open_file_raw_at_line(path, 42))
        self.assertEqual(api.window.editor.widget.lines, [42])
        self.assertEqual(api._pending_file_actions, {})

    def test_failed_open_cancels_pending_navigation(self):
        events = FakeEvents()
        commands = FakeCommands()
        api = PlatformApi(events, commands, None, None, None)
        api.window = FakeWindow()

        class FailingHandler:
            def open(self, path, api):
                api.events.publish(
                    Events.FILE_OPEN_FAILED,
                    FileOpenFailed(path=Path(path)),
                )
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
        events.publish(
            Events.FILE_OPENED,
            FileOpened(path=other, tab_id="ui.editor"),
        )
        self.assertEqual(api.window.editor.widget.lines, [])
        self.assertTrue(
            any(
                Path(record[0]).name == path.name
                for record in api._pending_file_actions.values()
            ),
            api._pending_file_actions,
        )

        events.publish(
            Events.FILE_OPENED,
            FileOpened(path=path, tab_id="ui.editor"),
        )
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

    def test_each_open_request_gets_its_own_request_id(self):
        events = FakeEvents()
        commands = FakeCommands()
        api = PlatformApi(events, commands, None, None, None)
        api.window = FakeWindow()

        class RequestAwareHandler:
            def __init__(self):
                self.ids = []

            def open(self, path, api):
                request_id = api.current_file_request_id()
                self.ids.append(request_id)
                api.events.publish(
                    Events.FILE_OPENED,
                    FileOpened(
                        path=Path(path),
                        tab_id="ui.editor",
                        request_id=request_id,
                    ),
                )
                return True

        handler = RequestAwareHandler()
        api.router.set_default(handler)
        path = Path("a.ltx")

        self.assertTrue(api.request_open_file_at_line(path, 42))
        self.assertTrue(api.request_open_file_at_line(path, 77))

        self.assertEqual(handler.ids, [1, 2])
        self.assertEqual(api.window.editor.widget.lines, [42, 77])
        self.assertEqual(api._pending_file_actions, {})

    def test_two_pending_requests_for_same_path_are_independent(self):
        events = FakeEvents()
        api = PlatformApi(events, FakeCommands(), None, None, None)
        api.window = FakeWindow()
        api.router.set_default(SilentHandler())
        path = Path("a.ltx")

        self.assertTrue(api.request_open_file_at_line(path, 42))
        self.assertTrue(api.request_open_file_at_line(path, 77))
        first_id, second_id = list(api._pending_file_actions)

        events.publish(
            Events.FILE_OPENED,
            FileOpened(path=path, tab_id="ui.editor", request_id=second_id),
        )
        self.assertEqual(api.window.editor.widget.lines, [77])
        self.assertIn(first_id, api._pending_file_actions)

        events.publish(
            Events.FILE_OPENED,
            FileOpened(path=path, tab_id="ui.editor", request_id=first_id),
        )
        self.assertEqual(api.window.editor.widget.lines, [77, 42])
        self.assertEqual(api._pending_file_actions, {})

    def test_failed_open_publishes_request_id_and_cancels_it(self):
        events = FakeEvents()
        api = PlatformApi(events, FakeCommands(), None, None, None)
        api.window = FakeWindow()
        seen = []
        events.subscribe(Events.FILE_OPEN_FAILED, seen.append)
        path = Path("missing.ltx")

        self.assertFalse(api.request_open_file_at_line(path, 10))

        self.assertEqual(len(seen), 1)
        self.assertIsInstance(seen[0], FileOpenFailed)
        self.assertGreater(seen[0].request_id, 0)
        self.assertEqual(api._pending_file_actions, {})
        self.assertEqual(api._pending_file_request_ids, {})

    def test_notify_failed_fills_request_id(self):
        events = FakeEvents()
        api = PlatformApi(events, FakeCommands(), None, None, None)
        api.window = FakeWindow()
        seen = []
        events.subscribe(Events.FILE_OPEN_FAILED, seen.append)
        path = Path("broken.xml")
        request_id = api._queue_file_action(path, "line", 5)

        api.notify_file_open_failed(path, "broken")

        self.assertEqual(seen[0].request_id, request_id)
        self.assertEqual(api._pending_file_actions, {})

    def test_request_tab_focuses_existing_tab(self):
        api = PlatformApi(FakeEvents(), FakeCommands(), None, None, None)
        api.window = FakeWindow()
        other = FakeWidget()
        api.window.editor.tabs["other"] = other

        self.assertTrue(api.request_tab("other"))
        self.assertIs(api.window.editor.current, other)
        self.assertFalse(api.request_tab("missing"))

    def test_request_window_falls_back_to_window_manager(self):
        api = PlatformApi(FakeEvents(), FakeCommands(), None, None, None)
        focused = []

        class Windows:
            def focus(self, key):
                focused.append(key)
                return key == "known"

        api._window_manager = Windows()

        self.assertTrue(api.request_window("known"))
        self.assertFalse(api.request_window("missing"))
        self.assertEqual(focused, ["known", "missing"])

    def test_request_file_open_returns_command_result(self):
        events = FakeEvents()
        commands = FakeCommands()
        api = PlatformApi(events, commands, None, None, None)
        commands.register("ui.open_file", lambda path: False)

        self.assertFalse(api.request_file_open(Path("a.ltx")))

        commands.register("ui.open_file", lambda path: True)
        self.assertTrue(api.request_file_open(Path("a.ltx")))


    def test_project_activation_locks_tabs_and_marks_busy(self):
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
            api.window = FakeWindow()
            api.project.create("busy", save_mode="root")

            api._begin_project_activation()
            self.assertTrue(api.window.editor.busy)

            api._end_project_activation()
            self.assertFalse(api.window.editor.busy)
            self.assertEqual(
                api.window.editor.calls,
                [
                    ("editable", False),
                    ("busy", True),
                    ("editable", True),
                    ("busy", False),
                ],
            )


    def test_busy_marks_are_cleared_without_active_project(self):
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
            api.window = FakeWindow()
            api.window.editor.set_tab_busy("stale.tab", True)

            api._end_project_activation()

            self.assertFalse(api.window.editor.busy)
            self.assertEqual(api.window.editor.tab_busy, set())


class ProjectDialogsRequireTreeTest(unittest.TestCase):
    def _api(self, root):
        api = PlatformApi(
            FakeEvents(),
            FakeCommands(),
            None,
            None,
            SimpleNamespace(
                projects_dir=Path(root) / "projects",
                app_dir=Path(root),
            ),
        )
        api.window = FakeWindow()
        errors = []
        api.show_error = errors.append
        return api, errors

    def test_create_project_requires_built_tree(self):
        with tempfile.TemporaryDirectory() as d:
            api, errors = self._api(d)

            api.create_project()

            self.assertEqual(
                errors,
                ["Дерево не построено: дождитесь окончания сборки."],
            )
            self.assertFalse(api.project.is_active)

    def test_load_project_requires_built_tree(self):
        with tempfile.TemporaryDirectory() as d:
            api, errors = self._api(d)

            api.load_project()

            self.assertEqual(
                errors,
                ["Дерево не построено: дождитесь окончания сборки."],
            )
            self.assertFalse(api.project.is_active)


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


class TreeReadyPayloadTest(unittest.TestCase):
    def test_tree_ready_is_lightweight_and_sets_workspace(self):
        with tempfile.TemporaryDirectory() as d:
            events = FakeEvents()
            api = PlatformApi(
                events,
                FakeCommands(),
                None,
                None,
                SimpleNamespace(
                    projects_dir=Path(d) / "projects",
                    app_dir=Path(d),
                ),
            )
            forest = _RestoreForest()
            forest.cache_id = "forest-1"
            seen = []
            events.subscribe(Events.TREE_READY, seen.append)

            payload = api.publish_tree_ready(forest)

            ref = api.workspace_forest()
            self.assertIsInstance(ref, ForestRef)
            self.assertEqual(ref.cache_id, "forest-1")
            self.assertIs(api._forest, forest)
            self.assertIsInstance(payload, TreeReady)
            self.assertEqual(payload.cache_id, "forest-1")
            self.assertFalse(hasattr(payload, "forest"))
            self.assertEqual(seen, [payload])

    def test_file_open_failed_is_published_by_platform(self):
        with tempfile.TemporaryDirectory() as d:
            events = FakeEvents()
            api = PlatformApi(
                events,
                FakeCommands(),
                None,
                None,
                SimpleNamespace(
                    projects_dir=Path(d) / "projects",
                    app_dir=Path(d),
                ),
            )
            seen = []
            events.subscribe(Events.FILE_OPEN_FAILED, seen.append)
            path = Path("gamedata/configs/items/test.xml")

            api.notify_file_open_failed(path, "broken")

            self.assertEqual(len(seen), 1)
            self.assertIsInstance(seen[0], FileOpenFailed)
            self.assertEqual(seen[0].path, path)
            self.assertEqual(seen[0].reason, "broken")


class TriggerActionIsolationTest(unittest.TestCase):
    def _api(self):
        return PlatformApi(
            EventBus(),
            FakeCommands(),
            None,
            None,
            None,
        )

    def test_callback_error_is_logged_and_does_not_escape(self):
        api = self._api()

        def fail():
            raise RuntimeError("action failed")

        self.assertFalse(api.trigger_action("demo.action", fail))
        log_text = "\n".join(api._log_buffer)
        self.assertIn("Ошибка действия demo.action", log_text)
        self.assertIn("RuntimeError", log_text)

        self.assertTrue(api.trigger_action("demo.action", lambda: True))

    def test_intercepted_action_does_not_call_callback(self):
        api = self._api()
        calls = []
        api.register_action_handler(
            "demo.action",
            lambda payload: calls.append("handler") or True,
            priority=10,
        )

        self.assertTrue(
            api.trigger_action(
                "demo.action",
                lambda: calls.append("callback"),
            )
        )
        self.assertEqual(calls, ["handler"])

    def test_intercepted_fallback_does_not_call_callback(self):
        api = self._api()
        calls = []
        api.register_action_handler(
            "ui.shortcut",
            lambda payload: calls.append("fallback") or True,
        )

        self.assertTrue(
            api.trigger_action(
                None,
                lambda: calls.append("callback"),
                fallback="ui.shortcut",
            )
        )
        self.assertEqual(calls, ["fallback"])


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
            # Лес в этом тесте не пересобирается; фиксируем ревизию и
            # снимок, для которых он актуален, чтобы проверить именно
            # механизм добавления/удаления виртуальных файлов.
            overlay = api.project_overlay()
            api._forest.overlay_revision = overlay.revision
            api._forest.cache_id = api.workspace.snapshot_id

            added, removed = api._restore_project_virtual_files()

            self.assertEqual((added, removed), (1, 0))
            self.assertIn(path, api._forest.files)
            self.assertEqual(api.services.resolver.invalidations, 1)

            api.project.remove_draft(path)
            overlay = api.project_overlay()
            api._forest.overlay_revision = overlay.revision
            api._forest.cache_id = api.workspace.snapshot_id
            added, removed = api._restore_project_virtual_files()

            self.assertEqual((added, removed), (0, 1))
            self.assertNotIn(path, api._forest.files)
            self.assertEqual(api.services.resolver.invalidations, 2)


class ProjectRollbackTest(unittest.TestCase):
    def test_rollback_preview_item_removes_text_draft(self):
        with tempfile.TemporaryDirectory() as d:
            api = PlatformApi(FakeEvents(), FakeCommands(), None, None, None)
            api.window = FakeWindow()
            api.project = ProjectService(Path(d) / "projects")
            api.project.create("Proj")

            source = Path(d) / "gamedata" / "configs" / "a.ltx"
            source.parent.mkdir(parents=True)
            source.write_text("[a]\nx = 1\n", encoding="cp1251")
            api.project.update_draft(source, "[a]\nx = 2\n")

            item = PreviewItem(
                title=source.name,
                source_path=source,
                plan=None,
                generated=None,
                rollback_paths=(source,),
            )

            self.assertTrue(api.rollback_preview_item(item))

            self.assertIsNone(api.project.get_draft(source))
            self.assertTrue(api._patch_preview_ready)


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
            from sdk.features.documents import ResolvedLineEdit

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

    def test_save_resolved_without_live_document_does_not_raise(self):
        """Ctrl+S в осиротевшей resolved-вкладке не должен ронять приложение."""
        with tempfile.TemporaryDirectory() as d:
            api, _events = self._api(d)
            tab_id = "base.resolved:system.ltx"
            root_path = Path(d) / "system.ltx"

            # Живого документа и черновика нет — сохранять нечего.
            api.save_resolved_document(tab_id)

            # Черновик есть, документ потерян (например, после смены
            # проекта): сохраняется именно черновик, без view и без
            # AttributeError.
            api.project.update_resolved_draft(
                tab_id,
                root_path,
                "[Res]system.ltx",
                "[sec]\nx = 1\n",
                "[sec]\nx = 2\n",
                (None, None, None),
            )
            api.save_resolved_document(tab_id)

            draft = api.project.get_resolved_draft(tab_id)
            self.assertEqual(draft.saved_text, "[sec]\nx = 2\n")

    def test_write_project_texts_rolls_back_on_failure(self):
        from unittest.mock import patch

        from app.projects import ProjectError

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

    def build_item_entry_from_block(self, forest, block_text, section,
                                    offset=0, log=None, overlay=None):
        return self.build_item_entry(
            forest,
            block_text,
            section,
            log=log,
            overlay=overlay,
        )

    def build_item_entry(self, forest, resolved_text, section, log=None,
                         overlay=None):
        from sdk.features.items import ItemEntry, ItemField

        return ItemEntry(
            section=str(section),
            fields=(ItemField("kind", "i_food", 2),),
        )


class _InlineTasks:
    def __init__(self):
        self.count = 0

    def run(self, fn, on_done=None, on_log=None, owner=None):
        self.count += 1
        try:
            result = fn(lambda _message: None)
        except Exception as exc:
            if on_done is not None:
                on_done(None, exc)
            return None
        if on_done is not None:
            on_done(result, None)
        return None


class _DeferredTasks:
    def __init__(self):
        self.calls = []
        self.cancelled = []

    def run(self, fn, on_done=None, on_log=None, owner=None):
        self.calls.append((fn, on_done))
        return object()

    def cancel_owner(self, owner):
        self.cancelled.append(str(owner))
        return 1


class ProjectAutosaveTest(unittest.TestCase):
    def _api(self, tasks):
        api = PlatformApi(FakeEvents(), FakeCommands(), tasks, None, None)
        api.window = FakeWindow()
        return api

    def _dirty_project(self, api, root):
        api.project.create("Auto")
        source = Path(root) / "configs" / "a.ltx"
        source.parent.mkdir(parents=True)
        source.write_text("[a]\nx = 1\n", encoding="cp1251")
        api.project.update_draft(source, "[a]\nx = 2\n")
        return source

    def test_autosave_runs_through_tasks_and_publishes_event(self):
        with tempfile.TemporaryDirectory() as d:
            tasks = _InlineTasks()
            api = self._api(tasks)
            source = self._dirty_project(api, d)
            events = []
            api.events.subscribe(Events.PROJECT_SAVED, events.append)

            api.autosave_project()

            self.assertEqual(tasks.count, 1)
            self.assertFalse(api.project.get_draft(source).is_dirty)
            self.assertEqual(len(events), 1)
            self.assertIn(source, api.window.editor.clean_paths)

    def test_repeated_requests_are_coalesced(self):
        with tempfile.TemporaryDirectory() as d:
            tasks = _DeferredTasks()
            api = self._api(tasks)
            source = self._dirty_project(api, d)

            api.autosave_project()
            api.autosave_project()
            api.autosave_project()

            self.assertEqual(len(tasks.calls), 1)
            self.assertTrue(api._autosave_pending)

            work, on_done = tasks.calls[0]
            saved = work(lambda _message: None)
            on_done(saved, None)

            self.assertEqual(len(tasks.calls), 2)
            self.assertFalse(api._autosave_pending)
            self.assertIn(source, api.window.editor.clean_paths)

    def test_stale_save_does_not_mark_editor_clean(self):
        with tempfile.TemporaryDirectory() as d:
            tasks = _DeferredTasks()
            api = self._api(tasks)
            source = self._dirty_project(api, d)

            api.autosave_project()
            work, on_done = tasks.calls[0]
            saved = work(lambda _message: None)

            api.project.update_draft(source, "[a]\nx = 3\n")
            on_done(saved, None)

            self.assertNotIn(source, api.window.editor.clean_paths)
            self.assertTrue(api.project.get_draft(source).is_dirty)

    def test_switched_project_is_not_autosaved(self):
        with tempfile.TemporaryDirectory() as d:
            tasks = _DeferredTasks()
            api = self._api(tasks)
            self._dirty_project(api, d)

            api.autosave_project()
            work, _on_done = tasks.calls[0]

            api.project.create("Other")
            other = Path(d) / "other" / "b.ltx"
            other.parent.mkdir(parents=True)
            other.write_text("[b]\nx = 1\n", encoding="cp1251")
            api.project.update_draft(other, "[b]\nx = 2\n")

            self.assertEqual(work(lambda _message: None), [])
            self.assertTrue(api.project.get_draft(other).is_dirty)

    def test_events_from_old_project_are_discarded(self):
        with tempfile.TemporaryDirectory() as d:
            tasks = _DeferredTasks()
            api = self._api(tasks)
            source = self._dirty_project(api, d)
            events = []
            api.events.subscribe(Events.PROJECT_SAVED, events.append)

            api.autosave_project()
            work, on_done = tasks.calls[0]
            saved = work(lambda _message: None)

            api.project.create("Other")
            on_done(saved, None)

            self.assertEqual(events, [])
            self.assertNotIn(source, api.window.editor.clean_paths)

    def test_removed_draft_event_is_not_published(self):
        with tempfile.TemporaryDirectory() as d:
            tasks = _DeferredTasks()
            api = self._api(tasks)
            source = self._dirty_project(api, d)
            events = []
            api.events.subscribe(Events.PROJECT_SAVED, events.append)

            api.autosave_project()
            work, on_done = tasks.calls[0]
            saved = work(lambda _message: None)
            api.project.remove_draft(source)
            on_done(saved, None)

            self.assertEqual(events, [])
            self.assertNotIn(source, api.window.editor.clean_paths)
            target = api.project.draft_store.text_target(
                api.project.active.root_dir,
                source,
            )
            self.assertFalse(target.is_file())

    def test_app_close_invalidates_running_autosave(self):
        with tempfile.TemporaryDirectory() as d:
            tasks = _DeferredTasks()
            api = self._api(tasks)
            source = self._dirty_project(api, d)

            api.autosave_project()
            before = api.project._autosave_epoch
            api.on_app_close()

            self.assertGreater(api.project._autosave_epoch, before)
            self.assertFalse(api._autosave_pending)
            self.assertFalse(api.project.get_draft(source).is_dirty)

    def test_project_activation_cancels_autosave_owner(self):
        with tempfile.TemporaryDirectory() as d:
            tasks = _DeferredTasks()
            api = self._api(tasks)
            self._dirty_project(api, d)
            project = api.project.active

            api.autosave_project()
            api._begin_project_activation()

            self.assertIn(project.id, tasks.cancelled)
            self.assertFalse(api._autosave_pending)


class DocumentTextReaderTest(unittest.TestCase):
    def test_document_text_uses_services_instead_of_core_import(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []

            class Services:
                def read_text(self, path):
                    calls.append(("text", Path(path)))
                    return "TEXT"

                def read_xml_text(self, path):
                    calls.append(("xml", Path(path)))
                    return "XML"

            api = PlatformApi(
                FakeEvents(),
                FakeCommands(),
                None,
                Services(),
                SimpleNamespace(
                    projects_dir=Path(d) / "projects",
                    app_dir=Path(d),
                ),
            )

            self.assertEqual(api.document_text(Path("a.ltx")), "TEXT")
            self.assertEqual(api.document_text(Path("a.xml")), "XML")
            self.assertEqual(
                calls,
                [("text", Path("a.ltx")), ("xml", Path("a.xml"))],
            )


if __name__ == "__main__":
    unittest.main()
