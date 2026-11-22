# -*- coding: utf-8 -*-
"""Владение регистрациями модулей (пункт 2.1 аудита)."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.qt_support import qapp
from tests.support import SRC  # noqa: F401

from PyQt5.QtWidgets import QWidget

from sdk import FileReader, RegistrationHandle, RegistrationScope
from sdk.features.projects import ProjectAssetType, ProjectSetting, ProjectSettingChoice
from ui.platform.sdk_bridge import CoreApiAdapter, build_module_context
from sdk.kernel import ModuleBase, ModuleContext, ModuleManifest
from sdk.contracts.events import Events
from ui.bus import CommandBus, EventBus
from ui.file_open_router import FileOpenRouter
from ui.input_bindings import MouseBindingManager, ShortcutManager
from ui.module_loader import ModuleLoader, ModuleRecord, ModuleState
from ui.platform.registration import RegistrationHub
from ui.platform_api import PlatformApi


def _make_context(api, events, commands, paths, services, module_name="demo"):
    return build_module_context(
        SimpleNamespace(name=module_name),
        sdk_version="0.1.0",
        events=events,
        commands=commands,
        ui=api,
        tasks=None,
        services=services,
        paths=paths,
    )


class RegistrationScopeTest(unittest.TestCase):
    def test_dispose_is_reversed_and_isolates_errors(self):
        calls = []
        scope = RegistrationScope("demo")
        scope.track(RegistrationHandle(lambda: calls.append("a")))
        scope.track(RegistrationHandle(lambda: 1 / 0))
        scope.track(RegistrationHandle(lambda: calls.append("c")))

        with self.assertLogs("sdk.kernel.registrations", level="ERROR"):
            self.assertEqual(scope.dispose(), 3)

        self.assertEqual(calls, ["c", "a"])
        self.assertTrue(scope.closed)
        self.assertEqual(scope.dispose(), 0)

    def test_late_registration_is_rejected_and_reverted(self):
        scope = RegistrationScope("demo")
        scope.dispose()

        called = []
        handle = RegistrationHandle(lambda: called.append(True))

        with self.assertRaises(RuntimeError):
            scope.track(handle)

        self.assertFalse(handle.active)
        self.assertEqual(called, [True])

    def test_handle_is_idempotent(self):
        calls = []
        handle = RegistrationHandle(lambda: calls.append(True))
        self.assertTrue(handle.unsubscribe())
        self.assertFalse(handle.unsubscribe())
        self.assertEqual(calls, [True])


class RegistrationHubTest(unittest.TestCase):
    def test_owner_handles_remove_only_their_registration(self):
        hub = RegistrationHub()
        first = hub.register_menu(
            "Файл", "Первый", lambda: None, owner="first"
        )
        second = hub.register_menu(
            "Файл", "Второй", lambda: None, owner="second"
        )
        hub.register_tree_context_action(
            "Дерево", lambda path: None, owner="first"
        )
        hub.register_file_action(
            "Вкладка", lambda path: None, owner="first"
        )

        first.unsubscribe()

        self.assertEqual(
            [item[1] for item in hub.menu_registrations],
            ["Второй"],
        )
        self.assertEqual(len(hub.context_actions), 1)
        self.assertEqual(len(hub.file_actions), 1)
        self.assertFalse(first.unsubscribe())

        second.unsubscribe()
        self.assertEqual(hub.menu_registrations, [])

    def test_listener_sees_menu_changes(self):
        hub = RegistrationHub()
        calls = []
        subscription = hub.add_listener(lambda: calls.append(True))

        handle = hub.register_menu(
            "Инструменты", "Демо", lambda: None, owner="demo"
        )
        handle.unsubscribe()
        subscription.unsubscribe()

        self.assertEqual(calls, [True, True])
        self.assertFalse(subscription.unsubscribe())


class FileOpenRouterOwnershipTest(unittest.TestCase):
    class Handler:
        priority = 10

        def can_open(self, path):
            return True

        def open(self, path, api):
            return True

    def test_handle_removes_handler_and_unregister_checks_owner(self):
        router = FileOpenRouter()
        handler = self.Handler()
        router.register(handler, owner="demo")

        self.assertFalse(router.unregister(handler, owner="other"))
        self.assertEqual(len(router._handlers), 1)

        handle = router.register(self.Handler(), owner="demo")
        handle.unsubscribe()
        self.assertEqual(len(router._handlers), 1)

        self.assertTrue(router.unregister(handler, owner="demo"))
        self.assertEqual(router._handlers, [])


class RegistryOwnershipTest(unittest.TestCase):
    ASSET = ProjectAssetType(
        key="demo.bin",
        label="Демо",
        extensions=(".bin",),
        vfs_root="demo",
    )
    SETTING = ProjectSetting(
        key="demo.mode",
        label="Демо",
        kind="choice",
        default="one",
        choices=(
            ProjectSettingChoice(value="one", label="Один"),
        ),
    )

    def _platform(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        paths = SimpleNamespace(projects_dir=root, app_dir=root)
        api = PlatformApi(
            EventBus(),
            CommandBus(),
            None,
            object(),
            paths,
        )
        return api

    def test_asset_and_setting_unregister_check_owner(self):
        api = self._platform()

        self.assertTrue(
            api.register_project_asset_type(self.ASSET, owner="demo")
        )
        self.assertTrue(
            api.register_project_setting(self.SETTING, owner="demo")
        )

        self.assertFalse(
            api.unregister_project_asset_type(
                self.ASSET.key,
                owner="other",
            )
        )
        self.assertFalse(
            api.unregister_project_setting(
                self.SETTING.key,
                owner="other",
            )
        )
        self.assertIn(
            self.ASSET.key,
            [item.key for item in api.project_asset_types()],
        )
        self.assertIsNotNone(
            api.project_setting_registry.get(self.SETTING.key)
        )

        self.assertTrue(
            api.unregister_project_asset_type(
                self.ASSET.key,
                owner="demo",
            )
        )
        self.assertTrue(
            api.unregister_project_setting(
                self.SETTING.key,
                owner="demo",
            )
        )
        self.assertNotIn(
            self.ASSET.key,
            [item.key for item in api.project_asset_types()],
        )
        self.assertIsNone(
            api.project_setting_registry.get(self.SETTING.key)
        )


class ContributorOwnershipTest(unittest.TestCase):
    class Provider:
        pass

    def test_contributor_is_removed_only_by_its_owner(self):
        provider = self.Provider()
        first = CoreApiAdapter(provider)
        second = CoreApiAdapter(provider)
        first.bind_owner("first")
        second.bind_owner("second")

        contributor = lambda request: ()
        first.register_section_graph_contributor(contributor)

        self.assertFalse(
            second.unregister_section_graph_contributor(contributor)
        )
        self.assertTrue(
            first.unregister_section_graph_contributor(contributor)
        )


class BindingsRemovalTest(unittest.TestCase):
    def setUp(self):
        self.app = qapp()
        self.widget = QWidget()
        self.addCleanup(self.widget.deleteLater)

    def test_shortcut_is_removed(self):
        manager = ShortcutManager(lambda *args: None)
        manager.attach(self.widget)

        handle = manager.register(
            "Ctrl+Alt+D",
            lambda: None,
            owner="demo",
        )
        registration = manager._registrations[0]
        self.assertIsNotNone(registration["object"])

        handle.unsubscribe()

        self.assertEqual(manager._registrations, [])
        self.assertIsNone(registration["object"])
        self.assertEqual(manager._installed, set())

    def test_mouse_filter_is_removed_with_last_binding(self):
        manager = MouseBindingManager(lambda *args: None)
        manager.attach(self.widget)

        first = manager.register(
            "left",
            "Ctrl",
            lambda event: False,
            owner="demo",
        )
        second = manager.register(
            "left",
            "Shift",
            lambda event: False,
            owner="demo",
        )
        self.assertIn("global", manager._filters)

        first.unsubscribe()
        self.assertIn("global", manager._filters)

        second.unsubscribe()
        self.assertEqual(manager._registrations, [])
        self.assertEqual(manager._filters, {})


class _ContextMixin:
    class Handler:
        priority = 100

        def can_open(self, path):
            return True

        def open(self, path, api):
            return True

    class Reader(FileReader):
        priority = 10

        def can_read(self, path, data=None):
            return False

        def read(self, path, data=None):
            return None

    class DemoModule(ModuleBase):
        manifest = ModuleManifest(name="demo")

        def __init__(self):
            self.deactivated = False

        def deactivate(self, ctx):
            self.deactivated = True

    def _context(self, module_name="demo"):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        paths = SimpleNamespace(projects_dir=root, app_dir=root)
        events = EventBus()
        commands = CommandBus()
        api = PlatformApi(events, commands, None, object(), paths)
        ctx = _make_context(api, events, commands, paths, object(), module_name)
        return api, events, commands, ctx


class EventOwnershipTest(unittest.TestCase):
    def _pair(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        paths = SimpleNamespace(projects_dir=root, app_dir=root)
        events = EventBus()
        commands = CommandBus()
        api = PlatformApi(events, commands, None, object(), paths)

        def make(name):
            return _make_context(api, events, commands, paths, object(), name)

        return api, events, make("first"), make("second")

    def test_module_unsubscribe_keeps_other_modules_subscription(self):
        api, events, first, second = self._pair()
        seen = []

        first.events.subscribe(
            "shared.event",
            lambda payload: seen.append("first"),
        )
        second.events.subscribe(
            "shared.event",
            lambda payload: seen.append("second"),
        )

        self.assertEqual(first.events.unsubscribe("shared.event"), 1)
        events.publish("shared.event", None)
        self.assertEqual(seen, ["second"])

        first.registrations.dispose()
        events.publish("shared.event", None)
        self.assertEqual(seen, ["second", "second"])

        second.registrations.dispose()
        self.assertFalse(events.has("shared.event"))

    def test_module_action_handler_replaces_platform_reaction(self):
        api, events, first, second = self._pair()
        calls = []

        api.register_action_handler(
            "draw.element",
            lambda payload: calls.append("platform") or False,
            priority=0,
        )
        second.ui.register_action_handler(
            "draw.element",
            lambda payload: calls.append("second") or True,
            priority=50,
        )

        self.assertTrue(events.dispatch("draw.element", None))
        self.assertEqual(calls, ["second"])

        first.registrations.dispose()
        second.registrations.dispose()


class ModuleContextCleanupTest(_ContextMixin, unittest.TestCase):
    def test_scope_removes_every_module_registration(self):
        api, events, commands, ctx = self._context()

        ctx.ui.register_menu("Инструменты", "Демо", lambda: None)
        ctx.ui.register_tree_context_action(
            "Демо",
            lambda path: None,
        )
        ctx.ui.register_file_action("Демо", lambda path: None)
        ctx.ui.register_open_handler(self.Handler())
        ctx.ui.register_shortcut("Ctrl+Alt+D", lambda: None)
        ctx.ui.register_mouse_binding(
            "left",
            "Ctrl",
            lambda event: False,
        )
        self.assertTrue(
            ctx.projects.register_project_asset_type(
                ProjectAssetType(
                    key="demo.bin",
                    label="Демо",
                    extensions=(".bin",),
                    vfs_root="demo",
                )
            )
        )
        self.assertTrue(
            ctx.projects.register_project_setting(
                ProjectSetting(
                    key="demo.mode",
                    label="Демо",
                    kind="choice",
                    default="one",
                    choices=(
                        ProjectSettingChoice(value="one", label="Один"),
                    ),
                )
            )
        )
        ctx.projects.register_file_reader(self.Reader())
        ctx.commands.register("demo.run", lambda: None)
        subscription = ctx.events.subscribe("demo.event", lambda payload: None)
        ctx.services.register_section_graph_contributor(
            lambda request: ()
        )

        self.assertTrue(api.menu_registrations)
        self.assertTrue(api.context_actions)
        self.assertTrue(api.file_actions)
        self.assertTrue(api.router._handlers)
        self.assertTrue(api._shortcut_manager._registrations)
        self.assertTrue(api._mouse_manager._registrations)
        self.assertIn(
            "demo.bin",
            [item.key for item in api.project_asset_types()],
        )
        self.assertIsNotNone(api.project_setting_registry.get("demo.mode"))
        self.assertTrue(api.file_readers.readers())
        self.assertTrue(commands.has("demo.run"))
        self.assertTrue(events.has("demo.event"))
        self.assertTrue(
            ctx.services._section_graph_contributors
        )

        self.assertEqual(ctx.registrations.dispose(), 12)

        self.assertEqual(api.menu_registrations, [])
        self.assertEqual(api.context_actions, [])
        self.assertEqual(api.file_actions, [])
        self.assertEqual(api.router._handlers, [])
        self.assertEqual(api._shortcut_manager._registrations, [])
        self.assertEqual(api._mouse_manager._registrations, [])
        self.assertNotIn(
            "demo.bin",
            [item.key for item in api.project_asset_types()],
        )
        self.assertIsNone(
            api.project_setting_registry.get("demo.mode")
        )
        self.assertEqual(api.file_readers.readers(), ())
        self.assertFalse(commands.has("demo.run"))
        self.assertFalse(events.has("demo.event"))
        self.assertFalse(subscription.active)
        self.assertEqual(
            ctx.services._section_graph_contributors,
            [],
        )

    def test_loader_disposes_scope_after_deactivate(self):
        api, _events, _commands, ctx = self._context()
        module = self.DemoModule()
        manifest = module.manifest
        loader = ModuleLoader(lambda _manifest: ctx)
        loader.records[manifest.name] = ModuleRecord(
            manifest=manifest,
            instance=module,
            package="demo",
            ctx=ctx,
            state=ModuleState.REGISTERED,
        )
        loader._order.append(manifest.name)

        ctx.ui.register_menu("Инструменты", "Демо", lambda: None)
        ctx.ui.register_tree_context_action(
            "Демо",
            lambda path: None,
        )

        loader.deactivate_all(log=lambda message: None)

        self.assertTrue(module.deactivated)
        self.assertEqual(api.menu_registrations, [])
        self.assertEqual(api.context_actions, [])
        self.assertTrue(ctx.registrations.closed)


class MainWindowMenuTest(_ContextMixin, unittest.TestCase):
    def test_module_menu_follows_registration_handle(self):
        from ui.main_window import MainWindow

        self.app = qapp()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        paths = SimpleNamespace(projects_dir=root, app_dir=root)
        events = EventBus()
        commands = CommandBus()
        services = SimpleNamespace(
            tree_query=SimpleNamespace(sort_nodes=lambda nodes: nodes),
        )
        api = PlatformApi(events, commands, None, services, paths)
        ctx = _make_context(api, events, commands, paths, services, "demo")

        handle = ctx.ui.register_menu(
            "Инструменты",
            "Демо-пункт",
            lambda: None,
        )
        window = MainWindow(api)

        def menu_titles():
            return [
                action.menu().title()
                for action in window.menuBar().actions()
                if action.menu() is not None
            ]

        def menu_labels():
            result = []
            for action in window.menuBar().actions():
                menu = action.menu()
                if menu is None:
                    continue
                result.extend(item.text() for item in menu.actions())
            return result

        self.assertIn("Инструменты", menu_titles())
        self.assertIn("Демо-пункт", menu_labels())

        handle.unsubscribe()

        self.assertNotIn("Инструменты", menu_titles())
        self.assertNotIn("Демо-пункт", menu_labels())

        window.close()


class OggModuleCleanupTest(_ContextMixin, unittest.TestCase):
    """Реальный модуль: scope убирает то, что deactivate не снимает."""

    def test_ogg_module_leaves_no_registrations(self):
        from modules.ogg_editor.module import OggEditorModule

        api, events, commands, ctx = self._context("ogg_editor")
        module = OggEditorModule()
        module.init(ctx)
        module.register(ctx)

        self.assertTrue(api.menu_registrations)
        self.assertTrue(commands.has("ogg_editor.window"))
        self.assertTrue(api.router._handlers)

        module.deactivate(ctx)
        ctx.registrations.dispose()

        self.assertEqual(api.menu_registrations, [])
        self.assertEqual(api.context_actions, [])
        self.assertEqual(api.router._handlers, [])
        self.assertFalse(commands.has("ogg_editor.window"))
        self.assertFalse(commands.has("ogg_editor.open"))
        self.assertFalse(commands.has("ogg_editor.add"))
        self.assertNotIn(
            "sound.ogg",
            [item.key for item in api.project_asset_types()],
        )
        self.assertIsNone(
            api.project_setting_registry.get("ogg_editor.save_mode")
        )
        self.assertEqual(
            events._subs.get(Events.PROJECT_SAVED, []),
            [],
        )


if __name__ == "__main__":
    unittest.main()
