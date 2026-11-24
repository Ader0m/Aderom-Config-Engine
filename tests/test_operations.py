# -*- coding: utf-8 -*-
"""Тесты реестра операций платформы."""

import unittest

from tests.support import SRC  # noqa: F401

from ui.operations import (
    CANCELLED,
    DONE,
    FAILED,
    RUNNING,
    OperationRegistry,
    OperationSnapshot,
    normalize_operation_key,
)


class OperationRegistryTest(unittest.TestCase):
    def _snapshot(self, key, state=RUNNING, owner="demo", **kwargs):
        return OperationSnapshot(
            key=key,
            owner=owner,
            title=key,
            state=state,
            **kwargs,
        )

    def test_register_update_and_finish(self):
        registry = OperationRegistry()
        registry.register(self._snapshot("a", done=1, total=3))
        self.assertTrue(registry.is_active("a"))
        self.assertEqual(registry.get("a").done, 1)

        registry.update(self._snapshot("a", done=2, total=3))
        self.assertEqual(registry.get("a").done, 2)

        registry.finish(self._snapshot("a", state=DONE, done=3, total=3))
        self.assertFalse(registry.is_active("a"))
        self.assertEqual(registry.get("a").state, DONE)

    def test_external_operation_keeps_started_at_and_cancel(self):
        registry = OperationRegistry()
        cancelled = []
        registry.upsert_external(
            "prebuild",
            owner="<platform>",
            title="Prebuild",
            state=RUNNING,
            done=1,
            total=4,
            cancel=lambda: cancelled.append(True),
        )
        first = registry.get("prebuild")
        self.assertGreater(first.started_at, 0)

        registry.upsert_external(
            "prebuild",
            owner="<platform>",
            title="Prebuild",
            state=RUNNING,
            done=2,
            total=4,
        )
        second = registry.get("prebuild")
        self.assertEqual(second.started_at, first.started_at)
        self.assertTrue(registry.cancel("prebuild"))
        self.assertEqual(cancelled, [True])

    def test_snapshots_sort_active_before_finished(self):
        registry = OperationRegistry()
        registry.finish(self._snapshot("old", state=DONE))
        registry.register(self._snapshot("new", state=RUNNING))
        keys = [item.key for item in registry.snapshots()]
        self.assertEqual(keys, ["new", "old"])

    def test_clear_finished_and_cancel_owner(self):
        registry = OperationRegistry()
        cancelled = []
        registry.register(
            self._snapshot("a", owner="demo"),
            cancel=lambda: cancelled.append("a"),
        )
        registry.register(
            self._snapshot("b", owner="other"),
            cancel=lambda: cancelled.append("b"),
        )
        registry.finish(self._snapshot("done", state=CANCELLED))

        self.assertEqual(registry.cancel_owner("demo"), 1)
        self.assertEqual(cancelled, ["a"])
        self.assertEqual(registry.clear_finished(), 1)
        self.assertIsNone(registry.get("done"))

    def test_finished_dependency_is_not_trimmed_while_waited_on(self):
        registry = OperationRegistry(max_finished=1)
        registry.register(
            self._snapshot(
                "waiting",
                owner="demo",
                dependencies=("vfs.prebuild",),
            )
        )
        registry.finish(
            self._snapshot("vfs.prebuild", state=FAILED, error="boom")
        )
        registry.finish(self._snapshot("other", state=DONE))

        snapshot = registry.get("vfs.prebuild")
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.state, FAILED)

    def test_clear_finished_keeps_dependency_of_active_task(self):
        registry = OperationRegistry()
        registry.register(
            self._snapshot(
                "waiting",
                owner="demo",
                dependencies=("vfs.prebuild",),
            )
        )
        registry.finish(
            self._snapshot("vfs.prebuild", state=FAILED, error="boom")
        )

        self.assertEqual(registry.clear_finished(), 0)
        self.assertIsNotNone(registry.get("vfs.prebuild"))
        self.assertEqual(registry.last_state("vfs.prebuild"), FAILED)

    def test_clear_finished_forgets_completed_key(self):
        registry = OperationRegistry()
        registry.finish(self._snapshot("old", state=DONE))

        self.assertEqual(registry.clear_finished(), 1)
        self.assertFalse(registry.has_seen("old"))
        self.assertIsNone(registry.get("old"))

    def test_finished_listener_fires_once_on_transition(self):
        registry = OperationRegistry()
        seen = []
        registry.add_finished_listener(seen.append)

        registry.upsert_external("vfs.prebuild", state=RUNNING)
        registry.upsert_external("vfs.prebuild", state=DONE)
        registry.upsert_external("vfs.prebuild", state=DONE)

        self.assertEqual([item.key for item in seen], ["vfs.prebuild"])

    def test_known_history_prefers_evicting_done(self):
        registry = OperationRegistry(max_finished=100, max_known=3)
        registry.upsert_external("failed", state=FAILED, error="boom")
        for index in range(3):
            registry.upsert_external(
                "done-%d" % index,
                state=DONE,
            )

        self.assertTrue(registry.has_seen("failed"))
        self.assertEqual(registry.last_state("failed"), FAILED)
        self.assertFalse(registry.has_seen("done-0"))

    def test_normalize_operation_key(self):
        self.assertEqual(
            normalize_operation_key("mod", "items.catalog"),
            "mod:items.catalog",
        )
        self.assertEqual(
            normalize_operation_key("mod", "mod.items.catalog"),
            "mod.items.catalog",
        )
        self.assertEqual(
            normalize_operation_key("mod", "vfs.rebuild"),
            "vfs.rebuild",
        )


if __name__ == "__main__":
    unittest.main()
