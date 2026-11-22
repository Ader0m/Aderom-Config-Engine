# -*- coding: utf-8 -*-
import io
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from tests.support import SRC  # noqa: F401

from sdk import PatchTargetInfo
from sdk.adapters import (
    CommandApiAdapter,
    CoreApiAdapter,
    EventApiAdapter,
)
from sdk.contracts import ModuleContext
from ui.bus import CommandBus, CommandError, EventBus


class _Ui:
    def log(self, message):
        pass


class _Tasks:
    def run(self, *args, **kwargs):
        pass


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

        with self.assertLogs("sdk.adapters", level="WARNING"):
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

    def test_module_context_wraps_buses(self):
        ctx = ModuleContext(
            "0.1.0",
            EventBus(),
            CommandBus(),
            _Ui(),
            _Tasks(),
            _Services(),
            None,
        )

        self.assertIsInstance(ctx.events, EventApiAdapter)
        self.assertIsInstance(ctx.commands, CommandApiAdapter)


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
