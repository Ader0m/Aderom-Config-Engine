# -*- coding: utf-8 -*-
import unittest

from tests.support import SRC  # noqa: F401

from sdk.adapters import CommandApiAdapter, EventApiAdapter
from sdk.contracts import ModuleContext
from ui.bus import CommandBus, EventBus


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


if __name__ == "__main__":
    unittest.main()
