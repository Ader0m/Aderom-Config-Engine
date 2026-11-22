# -*- coding: utf-8 -*-
"""Тесты шин событий и команд."""

import io
import unittest
from contextlib import redirect_stderr

from tests.support import SRC  # noqa: F401

from ui.bus import CommandBus, CommandError, EventBus
from sdk.contracts.events import PLATFORM_OWNER


class EventBusTest(unittest.TestCase):
    def test_publish_calls_all_subscribers_in_priority_order(self):
        bus = EventBus()
        calls = []

        bus.subscribe("e", lambda payload: calls.append("low"), priority=0)
        bus.subscribe("e", lambda payload: calls.append("high"), priority=10)
        bus.publish("e", None)

        self.assertEqual(calls, ["high", "low"])

    def test_equal_priority_keeps_registration_order(self):
        bus = EventBus()
        calls = []

        bus.subscribe("e", lambda payload: calls.append("a"))
        bus.subscribe("e", lambda payload: calls.append("b"))
        bus.publish("e", None)

        self.assertEqual(calls, ["a", "b"])

    def test_dispatch_stops_at_first_truthy_handler(self):
        bus = EventBus()
        calls = []

        bus.subscribe(
            "action",
            lambda payload: calls.append("low") or True,
            priority=0,
        )
        bus.subscribe(
            "action",
            lambda payload: calls.append("high") or True,
            priority=10,
        )

        self.assertTrue(bus.dispatch("action", None))
        self.assertEqual(calls, ["high"])

    def test_dispatch_continues_when_handlers_return_false(self):
        bus = EventBus()
        calls = []

        bus.subscribe("action", lambda payload: calls.append("a") or False)
        bus.subscribe("action", lambda payload: calls.append("b") or True)

        self.assertTrue(bus.dispatch("action", None))
        self.assertEqual(calls, ["a", "b"])

    def test_subscriber_error_is_isolated(self):
        bus = EventBus()
        seen = []

        bus.subscribe(
            "e",
            lambda payload: (_ for _ in ()).throw(RuntimeError("boom")),
            priority=10,
        )
        bus.subscribe("e", lambda payload: seen.append(payload))

        with redirect_stderr(io.StringIO()):
            bus.publish("e", 1)

        self.assertEqual(seen, [1])

    def test_unsubscribe_by_subscription_and_by_handler(self):
        bus = EventBus()
        seen = []

        def handler(payload):
            seen.append(payload)

        sub = bus.subscribe("e", handler)
        bus.publish("e", 1)
        sub.unsubscribe()
        sub.unsubscribe()
        bus.publish("e", 2)
        self.assertEqual(seen, [1])

        bus.subscribe("e", handler)
        self.assertTrue(bus.has("e"))
        bus.unsubscribe("e", handler)
        self.assertFalse(bus.has("e"))
        bus.publish("e", 3)
        self.assertEqual(seen, [1])

    def test_clear_removes_subscribers(self):
        bus = EventBus()
        bus.subscribe("e", lambda payload: None)
        bus.clear("e")
        self.assertFalse(bus.has("e"))

    def test_unsubscribe_is_owner_scoped(self):
        bus = EventBus()
        seen = []

        bus.subscribe("e", lambda payload: seen.append("a"), owner="a")
        bus.subscribe("e", lambda payload: seen.append("b"), owner="b")

        self.assertEqual(bus.unsubscribe("e", owner="a"), 1)
        bus.publish("e", None)
        self.assertEqual(seen, ["b"])
        self.assertTrue(bus.has("e", owner="b"))
        self.assertFalse(bus.has("e", owner="a"))

        # Без owner снимаются только платформенные подписки.
        self.assertEqual(bus.unsubscribe("e"), 0)
        bus.publish("e", None)
        self.assertEqual(seen, ["b", "b"])

        self.assertEqual(bus.unsubscribe("e", owner="b"), 1)
        self.assertFalse(bus.has("e"))

    def test_clear_can_remove_one_owner(self):
        bus = EventBus()
        bus.subscribe("e", lambda payload: None, owner="a")
        bus.subscribe("e", lambda payload: None, owner="b")

        self.assertEqual(bus.clear("e", owner="a"), 1)
        self.assertTrue(bus.has("e", owner="b"))
        self.assertFalse(bus.has("e", owner="a"))

        self.assertEqual(bus.clear("e"), 1)
        self.assertFalse(bus.has("e"))

    def test_dispatch_lets_a_module_replace_platform_reaction(self):
        bus = EventBus()
        calls = []

        bus.subscribe(
            "draw.element",
            lambda payload: calls.append("platform") or False,
            priority=0,
            owner=PLATFORM_OWNER,
        )
        bus.subscribe(
            "draw.element",
            lambda payload: calls.append("module") or True,
            priority=100,
            owner="demo",
        )

        self.assertTrue(bus.dispatch("draw.element", None))
        self.assertEqual(calls, ["module"])

    def test_lower_priority_module_handler_does_not_replace(self):
        bus = EventBus()
        calls = []

        bus.subscribe(
            "draw.element",
            lambda payload: calls.append("platform") or False,
            priority=10,
            owner=PLATFORM_OWNER,
        )
        bus.subscribe(
            "draw.element",
            lambda payload: calls.append("module") or True,
            priority=0,
            owner="demo",
        )

        self.assertTrue(bus.dispatch("draw.element", None))
        self.assertEqual(calls, ["platform", "module"])


class CommandBusTest(unittest.TestCase):
    def test_register_execute_and_unregister(self):
        bus = CommandBus()
        bus.register("test.command", lambda value: value * 2)

        self.assertTrue(bus.has("test.command"))
        self.assertEqual(bus.execute("test.command", 3), 6)
        self.assertTrue(bus.unregister("test.command"))
        self.assertFalse(bus.has("test.command"))

    def test_missing_command_raises(self):
        bus = CommandBus()
        with self.assertRaises(CommandError):
            bus.execute("test.missing")

    def test_handler_error_propagates(self):
        bus = CommandBus()

        def fail():
            raise RuntimeError("boom")

        bus.register("test.fail", fail)
        with self.assertRaises(RuntimeError):
            bus.execute("test.fail")


if __name__ == "__main__":
    unittest.main()
