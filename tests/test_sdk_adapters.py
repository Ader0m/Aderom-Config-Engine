# -*- coding: utf-8 -*-
import io
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from tests.support import SRC  # noqa: F401

from sdk.features.patches import PatchTargetInfo
from sdk.ui import DockSpec, TabSpec, WindowSpec
from ui.platform.sdk_bridge import (
    CommandApiAdapter,
    CoreApiAdapter,
    DocumentApiAdapter,
    EventApiAdapter,
    TaskApiAdapter,
    UiApiAdapter,
)
from sdk.kernel import ModuleContext
from ui.bus import CommandBus, CommandError, EventBus


class _Ui:
    def log(self, message):
        pass


class _Tasks:
    def run(self, *args, **kwargs):
        pass


class _RecordingTasks:
    def __init__(self):
        self.calls = []

    def run(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return "handle"


class _OwnerPlatform:
    def __init__(self):
        self.calls = []

    def open_window(self, spec, widget, owner=None):
        self.calls.append(("open_window", spec.key, owner))
        return True

    def open_tab(self, spec, widget, owner=None):
        self.calls.append(("open_tab", spec.id, owner))
        return True

    def open_dock(self, spec, widget, owner=None):
        self.calls.append(("open_dock", spec.key, owner))
        return True

    def save_state(self, state_key, state):
        self.calls.append(("save_state", state_key, state))
        return True

    def load_state(self, state_key):
        self.calls.append(("load_state", state_key, None))
        return {}

    def close_editor_tab(self, tab_id, owner=None):
        self.calls.append(("close_editor_tab", tab_id, owner))
        return True

    def close_module_window(self, key, owner=None):
        self.calls.append(("close_module_window", key, owner))
        return True

    def show_text(self, text, label, **kwargs):
        self.calls.append(("show_text", label, kwargs.get("owner")))
        return True

    def set_editor_tab_dirty(self, tab_id, dirty, owner=None):
        self.calls.append(("set_editor_tab_dirty", tab_id, owner))
        return True

    def set_editor_tab_busy(self, tab_id, busy, owner=None):
        self.calls.append(("set_editor_tab_busy", tab_id, owner))
        return True


class _DocumentOwnerPlatform:
    def __init__(self):
        self.calls = []

    def _record(self, name, owner):
        self.calls.append((name, owner))
        return True

    def apply_resolved_line_edit(self, tab_id, action, line_number, text="",
                                 owner=None):
        return self._record("line_edit", owner)

    def apply_resolved_field_edit(self, tab_id, section, key, value,
                                  owner=None):
        return self._record("field_edit", owner)

    def rename_resolved_field(self, tab_id, section, old_key, new_key, value,
                              owner=None):
        return self._record("rename", owner)

    def apply_resolved_field_edit_async(self, tab_id, section, key, value,
                                        on_done=None, owner=None):
        return self._record("field_edit_async", owner)

    def rename_resolved_field_async(self, tab_id, section, old_key, new_key,
                                    value, on_done=None, owner=None):
        return self._record("rename_async", owner)

    def undo_resolved_document(self, tab_id, owner=None):
        return self._record("undo", owner)

    def redo_resolved_document(self, tab_id, owner=None):
        return self._record("redo", owner)

    def retain_resolved_document(self, tab_id, owner=None):
        return self._record("retain", owner)

    def release_resolved_document(self, tab_id, owner=None):
        return self._record("release", owner)

    def update_resolved_draft(self, tab_id, root_path, title, original_text,
                              current_text, line_sources=(), line_edit=None,
                              owner=None):
        return self._record("update_draft", owner)

    def sync_resolved_tab(self, tab_id, text, line_sources, root_path,
                          owner=None):
        return self._record("sync_tab", owner)

    def ensure_resolved_document(self, tab_id, root_path, title="", text="",
                                 line_sources=(), owner=None):
        return self._record("ensure", owner)


class _Services:
    pass


class SdkAdapterTest(unittest.TestCase):
    def test_event_adapter_delegates_and_unsubscribes(self):
        bus = EventBus()
        events = EventApiAdapter(bus)
        seen = []

        sub = events.subscribe("test.event", lambda payload: seen.append(payload))
        events.publish("test.event", 1)
        sub.unsubscribe()
        events.publish("test.event", 2)

        self.assertEqual(seen, [1])
        self.assertFalse(events.has("test.event"))

    def test_event_adapter_priority_dispatch(self):
        bus = EventBus()
        events = EventApiAdapter(bus)
        calls = []

        events.subscribe(
            "test.action",
            lambda payload: calls.append("low") or False,
            priority=0,
        )
        events.subscribe(
            "test.action",
            lambda payload: calls.append("high") or True,
            priority=100,
        )

        self.assertTrue(events.dispatch("test.action", None))
        self.assertEqual(calls, ["high"])

    def test_command_adapter_delegates(self):
        bus = CommandBus()
        commands = CommandApiAdapter(bus)
        commands.register("test.command", lambda value: value * 2)

        self.assertTrue(commands.has("test.command"))
        self.assertEqual(commands.execute("test.command", 3), 6)

    def test_event_priority_falls_through_when_handler_returns_false(self):
        bus = EventBus()
        events = EventApiAdapter(bus)
        calls = []

        events.subscribe(
            "test.action",
            lambda payload: calls.append("low") or True,
            priority=0,
        )
        events.subscribe(
            "test.action",
            lambda payload: calls.append("high") or False,
            priority=100,
        )

        self.assertTrue(events.dispatch("test.action", None))
        self.assertEqual(calls, ["high", "low"])

    def test_event_equal_priority_keeps_registration_order(self):
        bus = EventBus()
        events = EventApiAdapter(bus)
        calls = []

        events.subscribe("test.event", lambda payload: calls.append("a"))
        events.subscribe("test.event", lambda payload: calls.append("b"))
        events.publish("test.event", None)

        self.assertEqual(calls, ["a", "b"])

    def test_event_adapter_isolates_subscriber_errors(self):
        bus = EventBus()
        events = EventApiAdapter(bus)
        seen = []

        events.subscribe(
            "test.event",
            lambda payload: (_ for _ in ()).throw(RuntimeError("boom")),
            priority=10,
        )
        events.subscribe(
            "test.event",
            lambda payload: seen.append(payload),
        )

        with redirect_stderr(io.StringIO()):
            events.publish("test.event", 7)

        self.assertEqual(seen, [7])

    def test_subscription_unsubscribe_is_idempotent(self):
        bus = EventBus()
        events = EventApiAdapter(bus)
        seen = []

        sub = events.subscribe("test.event", lambda payload: seen.append(payload))
        sub.unsubscribe()
        sub.unsubscribe()
        events.publish("test.event", 1)

        self.assertEqual(seen, [])

    def test_event_adapters_unsubscribe_only_their_owner(self):
        bus = EventBus()
        first = EventApiAdapter(bus)
        second = EventApiAdapter(bus)
        first.bind_owner("first")
        second.bind_owner("second")
        seen = []

        first.subscribe("test.event", lambda payload: seen.append("first"))
        second.subscribe("test.event", lambda payload: seen.append("second"))

        self.assertEqual(first.unsubscribe("test.event"), 1)
        bus.publish("test.event", None)

        self.assertEqual(seen, ["second"])
        self.assertTrue(bus.has("test.event", owner="second"))
        self.assertFalse(bus.has("test.event", owner="first"))

    def test_publish_warns_on_foreign_namespace(self):
        bus = EventBus()
        events = EventApiAdapter(bus)
        events.bind_owner("demo")

        with self.assertLogs("ui.platform.sdk_bridge", level="WARNING"):
            events.publish("other_module.event", None)

    def test_command_adapter_reports_missing_and_failing_handlers(self):
        bus = CommandBus()
        commands = CommandApiAdapter(bus)

        with self.assertRaises(CommandError):
            commands.execute("test.missing")

        def fail(value):
            raise RuntimeError("boom")

        commands.register("test.fail", fail)
        with self.assertRaises(RuntimeError):
            commands.execute("test.fail", 1)

    def test_module_context_uses_adapters(self):
        ctx = ModuleContext(
            "0.1.0",
            module_id="test",
            events=EventApiAdapter(EventBus()),
            commands=CommandApiAdapter(CommandBus()),
            ui=UiApiAdapter(_Ui()),
            tasks=TaskApiAdapter(_Tasks()),
            services=CoreApiAdapter(_Services()),
            paths=None,
        )

        self.assertIsInstance(ctx.events, EventApiAdapter)
        self.assertIsInstance(ctx.commands, CommandApiAdapter)

    def test_ui_adapter_passes_bound_owner_to_windows_and_tabs(self):
        platform = _OwnerPlatform()
        ui = UiApiAdapter(platform)
        ui.bind_owner("demo")

        ui.open_window(
            WindowSpec("demo.window", "Window", kind="floating"),
            object(),
        )
        ui.open_tab(
            TabSpec("demo.tab", "Tab"),
            object(),
        )
        ui.open_dock(
            DockSpec("demo.dock", "Dock"),
            object(),
        )
        ui.close_editor_tab("demo.tab")
        ui.close_module_window("demo.window")
        ui.show_text("text", "Label", source_path="a.ltx")
        ui.set_editor_tab_dirty("demo.tab", True)
        ui.set_editor_tab_busy("demo.tab", True)
        ui.save_state("state.key", {"a": 1})
        ui.load_state("state.key")

        self.assertEqual(
            [call[0] for call in platform.calls],
            [
                "open_window",
                "open_tab",
                "open_dock",
                "close_editor_tab",
                "close_module_window",
                "show_text",
                "set_editor_tab_dirty",
                "set_editor_tab_busy",
                "save_state",
                "load_state",
            ],
        )
        self.assertTrue(
            all(call[-1] == "demo" for call in platform.calls[:8])
        )

    def test_task_adapter_forwards_operation_contract(self):
        service = _RecordingTasks()
        tasks = TaskApiAdapter(service)
        tasks.bind_owner("demo")

        result = tasks.run(
            lambda _log: None,
            operation_key="demo.build",
            title="Build",
            depends_on=("vfs.rebuild",),
            priority=5,
        )

        self.assertEqual(result, "handle")
        self.assertEqual(service.calls[0][1]["owner"], "demo")
        self.assertEqual(service.calls[0][1]["operation_key"], "demo.build")
        self.assertEqual(service.calls[0][1]["title"], "Build")
        self.assertEqual(
            service.calls[0][1]["depends_on"],
            ("vfs.rebuild",),
        )
        self.assertEqual(service.calls[0][1]["priority"], 5)

    def test_document_adapter_passes_bound_owner_to_mutations(self):
        platform = _DocumentOwnerPlatform()
        documents = DocumentApiAdapter(platform)
        documents.bind_owner("demo")

        self.assertTrue(documents.apply_resolved_line_edit(
            "tab", "replace", 2, "x = 1",
        ))
        self.assertTrue(documents.apply_resolved_field_edit(
            "tab", "section", "key", "value",
        ))
        self.assertTrue(documents.rename_resolved_field(
            "tab", "section", "old", "new", "value",
        ))
        self.assertTrue(documents.apply_resolved_field_edit_async(
            "tab", "section", "key", "value",
        ))
        self.assertTrue(documents.rename_resolved_field_async(
            "tab", "section", "old", "new", "value",
        ))
        self.assertTrue(documents.undo_resolved_document("tab"))
        self.assertTrue(documents.redo_resolved_document("tab"))
        self.assertTrue(documents.retain_resolved_document("tab"))
        self.assertTrue(documents.release_resolved_document("tab"))
        self.assertTrue(documents.update_resolved_draft(
            "tab",
            Path("root.ltx"),
            "Title",
            "old",
            "new",
        ))
        self.assertTrue(documents.sync_resolved_tab(
            "tab",
            "text",
            (),
            Path("root.ltx"),
        ))
        self.assertTrue(documents.ensure_resolved_document(
            "tab",
            Path("root.ltx"),
        ))

        self.assertEqual(
            [call[0] for call in platform.calls],
            [
                "line_edit",
                "field_edit",
                "rename",
                "field_edit_async",
                "rename_async",
                "undo",
                "redo",
                "retain",
                "release",
                "update_draft",
                "sync_tab",
                "ensure",
            ],
        )
        self.assertTrue(
            all(call[-1] == "demo" for call in platform.calls)
        )


class CanonicalRedirectTest(unittest.TestCase):
    class Provider:
        def __init__(self):
            self.calls = []

        def resolve_scan_dir(self, config_dir):
            self.calls.append(("scan", config_dir))
            return Path(config_dir) / "scan"

        def relative_path(self, forest, path):
            self.calls.append(("relative", forest, path))
            return "rel"

        def game_root(self, forest):
            self.calls.append(("root", forest))
            return Path("gamedata")

        def patch_target_info(self, forest, path):
            self.calls.append(("target", forest, path))
            return type("Target", (), {
                "root_path": Path("root.ltx"),
                "candidates": (Path("root.ltx"),),
                "reason": "test",
                "ambiguous": False,
            })()

        def run_resolver(self, forest, config_dir, log=None):
            self.calls.append(("resolver", forest, config_dir, log))
            return "done"

        def read_text(self, path):
            self.calls.append(("read", path))
            return "text"

        def open_external_editor(self, path):
            self.calls.append(("editor", path))
            return True, ""

    def test_adapter_calls_only_canonical_methods(self):
        provider = self.Provider()
        adapter = CoreApiAdapter(provider)
        forest = object()

        self.assertEqual(adapter.resolve_scan_dir("cfg"), Path("cfg/scan"))
        self.assertEqual(adapter.relative_path(forest, "a.ltx"), "rel")
        self.assertEqual(adapter.game_root(forest), Path("gamedata"))
        info = adapter.patch_target_info(forest, "a.ltx")
        self.assertIsInstance(info, PatchTargetInfo)
        self.assertEqual(info.root_path, Path("root.ltx"))
        self.assertEqual(adapter.run_resolver(forest, "cfg"), "done")
        self.assertEqual(adapter.read_text("a.ltx"), "text")
        self.assertEqual(
            adapter.open_external_editor("a.ltx"),
            (True, ""),
        )
        self.assertEqual(
            [call[0] for call in provider.calls],
            [
                "scan",
                "relative",
                "root",
                "target",
                "resolver",
                "read",
                "editor",
            ],
        )

    def test_missing_canonical_method_is_not_replaced(self):
        adapter = CoreApiAdapter(object())

        with self.assertRaises(AttributeError):
            adapter.resolve_scan_dir("cfg")
        with self.assertRaises(AttributeError):
            adapter.game_root(object())
        with self.assertRaises(AttributeError):
            adapter.read_text("a.ltx")

    def test_contributors_are_shared_without_setattr(self):
        class SlottedProvider:
            __slots__ = ("__weakref__",)

        provider = SlottedProvider()
        first = CoreApiAdapter(provider)
        second = CoreApiAdapter(provider)

        first.register_section_graph_contributor(lambda request: ())

        self.assertIs(
            first._section_graph_contributors,
            second._section_graph_contributors,
        )
        self.assertFalse(
            hasattr(provider, "_sdk_section_graph_contributors")
        )

    def test_contributors_are_shared_for_provider_without_weakref(self):
        class HardProvider:
            __slots__ = ()

        provider = HardProvider()
        first = CoreApiAdapter(provider)
        second = CoreApiAdapter(provider)

        first.register_section_graph_contributor(lambda request: ())

        self.assertIs(
            first._section_graph_contributors,
            second._section_graph_contributors,
        )
        self.assertFalse(
            hasattr(provider, "_sdk_section_graph_contributors")
        )


if __name__ == "__main__":
    unittest.main()
