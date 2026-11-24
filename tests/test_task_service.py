# -*- coding: utf-8 -*-
"""Тесты фоновых задач и доставки колбэков."""

import threading
import time
import unittest

from tests.support import SRC  # noqa: F401
from tests.qt_support import qapp

from PyQt5.QtCore import QEventLoop, QTimer

from ui.operations import (
    CANCELLED,
    DONE,
    FAILED,
    QUEUED,
    RUNNING,
    OperationRegistry,
)
from ui.tasks import TaskService, current_task


class _BarrierTaskService(TaskService):
    """Расширяет окно между проверкой зависимости и постановкой в pending."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.check_entered = threading.Event()
        self.check_release = threading.Event()
        self._barrier_used = False

    def _dependencies_blocked(self, handle):
        blocked = super()._dependencies_blocked(handle)
        if blocked and not self._barrier_used:
            self._barrier_used = True
            self.check_entered.set()
            self.check_release.wait(0.2)
        return blocked


class TaskServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qapp()

    def _wait_until(self, predicate, timeout=3000):
        loop = QEventLoop()
        timer = QTimer()
        timer.setInterval(10)

        def check():
            if predicate():
                loop.quit()

        timer.timeout.connect(check)
        timer.start()
        QTimer.singleShot(timeout, loop.quit)
        loop.exec_()
        timer.stop()
        return predicate()

    def _wait_finished(self, *handles, timeout=3000):
        return self._wait_until(
            lambda: all(handle.is_finished for handle in handles),
            timeout=timeout,
        )

    def test_success_callback_receives_result_in_ui_thread(self):
        service = TaskService()
        done = []
        logs = []
        callback_threads = []

        def on_done(result, error):
            callback_threads.append(threading.current_thread())
            done.append((result, error))

        service.run(
            lambda log: (log("progress"), 42)[1],
            on_done=on_done,
            on_log=logs.append,
        )
        self.assertTrue(
            self._wait_until(lambda: done),
            "on_done не был вызван",
        )

        self.assertEqual(done, [(42, None)])
        self.assertIn("progress", logs)
        self.assertTrue(
            all(
                thread is threading.main_thread()
                for thread in callback_threads
            )
        )

    def test_wait_does_not_return_before_on_done(self):
        service = TaskService(max_workers=1)
        self.addCleanup(service.shutdown, 1000)
        order = []
        wait_result = []
        had_error = []

        def on_done(_result, error):
            had_error.append(error)
            order.append("callback")

        handle = service.run(
            lambda _log: 1,
            owner="demo",
            operation_key="demo.wait-final",
            on_done=on_done,
        )

        def waiter():
            wait_result.append(handle.wait(2000))
            order.append("wait")

        thread = threading.Thread(target=waiter)
        thread.start()
        self.assertTrue(
            self._wait_until(
                lambda: order == ["callback", "wait"] and not thread.is_alive()
            )
        )
        self.assertEqual(wait_result, [True])
        self.assertEqual(had_error, [None])

    def test_error_callback_receives_exception(self):
        service = TaskService()
        done = []
        logs = []

        def fail(log):
            raise RuntimeError("boom")

        service.run(
            fail,
            on_done=lambda result, error: done.append((result, error)),
            on_log=logs.append,
        )
        self.assertTrue(
            self._wait_until(lambda: done),
            "on_done не был вызван при ошибке",
        )

        result, error = done[0]
        self.assertIsNone(result)
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(str(error), "boom")
        self.assertTrue(logs)
        self.assertIn("RuntimeError", logs[0])

    def test_owner_tracks_and_waits_its_workers(self):
        service = TaskService()
        started = threading.Event()
        release = threading.Event()

        def work(_log):
            started.set()
            release.wait(2)
            return 1

        service.run(work, owner="demo")
        self.assertTrue(started.wait(2))
        self.assertEqual(len(service.workers_for("demo")), 1)
        self.assertEqual(service.workers_for("other"), ())

        self.assertEqual(service.cancel_owner("demo"), 1)
        release.set()
        self.assertTrue(
            self._wait_until(lambda: not service.workers_for("demo"))
        )
        self.assertTrue(service.wait_owner("demo", timeout_ms=100))
        self.assertEqual(service.workers_for("demo"), ())

    def test_on_log_error_does_not_break_task(self):
        service = TaskService()
        done = []

        def bad_log(_message):
            raise RuntimeError("log failed")

        with self.assertLogs("ui.tasks", level="ERROR"):
            service.run(
                lambda log: (log("progress"), 42)[1],
                on_done=lambda result, error: done.append((result, error)),
                on_log=bad_log,
            )
            self.assertTrue(
                self._wait_until(lambda: done),
                "on_done не был вызван после ошибки on_log",
            )

        self.assertEqual(done, [(42, None)])

    def test_on_done_error_is_logged_and_worker_is_cleaned(self):
        service = TaskService()
        called = []

        def bad_done(_result, _error):
            called.append(True)
            raise RuntimeError("done failed")

        with self.assertLogs("ui.tasks", level="ERROR"):
            service.run(lambda _log: 1, on_done=bad_done)
            self.assertTrue(self._wait_until(lambda: called))
            self.assertTrue(self._wait_until(
                lambda: not service._workers,
            ))

        done = []
        service.run(
            lambda _log: 2,
            on_done=lambda result, error: done.append(result),
        )
        self.assertTrue(self._wait_until(lambda: done))
        self.assertEqual(done, [2])

    def test_wait_owner_uses_single_deadline(self):
        service = TaskService(max_workers=2)
        release = threading.Event()
        self.addCleanup(service.shutdown, 1000)
        self.addCleanup(release.set)
        started = threading.Event()
        count = [0]
        lock = threading.Lock()

        def work(_log):
            with lock:
                count[0] += 1
                if count[0] == 2:
                    started.set()
            release.wait(5)
            return 1

        handles = [
            service.run(
                work,
                owner="demo",
                operation_key="demo.wait-owner-%d" % index,
            )
            for index in range(2)
        ]
        self.assertTrue(started.wait(2))

        began = time.monotonic()
        self.assertFalse(service.wait_owner("demo", timeout_ms=200))
        self.assertLess(time.monotonic() - began, 0.8)

        release.set()
        self.assertTrue(self._wait_finished(*handles))

    def test_shutdown_uses_single_deadline(self):
        service = TaskService(max_workers=4)
        release = threading.Event()
        self.addCleanup(service.shutdown, 1000)
        self.addCleanup(release.set)
        started = threading.Event()
        count = [0]
        lock = threading.Lock()

        def work(_log):
            with lock:
                count[0] += 1
                if count[0] == 4:
                    started.set()
            release.wait(5)
            return 1

        handles = [
            service.run(
                work,
                owner="demo",
                operation_key="demo.shutdown-%d" % index,
            )
            for index in range(4)
        ]
        self.assertTrue(started.wait(2))

        began = time.monotonic()
        self.assertFalse(service.shutdown(timeout_ms=200))
        self.assertLess(time.monotonic() - began, 0.8)

        release.set()
        self.assertTrue(self._wait_finished(*handles))

    def test_shutdown_reports_unfinished_task(self):
        service = TaskService()
        started = threading.Event()
        release = threading.Event()

        def work(_log):
            started.set()
            release.wait(3)

        handle = service.run(work)
        self.assertTrue(started.wait(2))
        try:
            self.assertFalse(service.shutdown(timeout_ms=10))
        finally:
            release.set()
            self.assertTrue(
                self._wait_until(lambda: handle.is_finished)
            )

    def test_operation_key_single_flight(self):
        service = TaskService()
        started = threading.Event()
        release = threading.Event()
        calls = []
        done = []

        def work(_log):
            calls.append("started")
            started.set()
            release.wait(3)
            return 1

        first = service.run(
            work,
            owner="demo",
            operation_key="demo.operation",
            title="Demo",
            on_done=lambda result, error: done.append(("first", result, error)),
        )
        self.assertTrue(started.wait(2))
        second = service.run(
            work,
            owner="demo",
            operation_key="demo.operation",
            title="Demo",
            on_done=lambda result, error: done.append(("second", result, error)),
        )

        self.assertIs(first, second)
        self.assertEqual(first.duplicate_count, 1)
        self.assertEqual(calls, ["started"])
        self.assertTrue(service.operations.is_active("demo.operation"))

        release.set()
        self.assertTrue(self._wait_until(lambda: first.state == DONE))
        self.assertFalse(service.operations.is_active("demo.operation"))
        self.assertEqual(first.result, 1)
        self.assertEqual(
            done,
            [
                ("first", 1, None),
                ("second", 1, None),
            ],
        )

    def test_depends_on_waits_for_dependency(self):
        service = TaskService()
        release = threading.Event()
        started = threading.Event()
        order = []

        def dependency(_log):
            order.append("dependency-start")
            started.set()
            release.wait(3)
            order.append("dependency-done")
            return "dep"

        def dependent(_log):
            order.append("dependent")
            return "result"

        dependency_handle = service.run(
            dependency,
            owner="demo",
            operation_key="demo.dependency",
        )
        dependent_handle = service.run(
            dependent,
            owner="demo",
            operation_key="demo.dependent",
            depends_on=("demo.dependency",),
        )

        self.assertEqual(dependent_handle.state, QUEUED)
        self.assertTrue(started.wait(2))
        self.assertEqual(order, ["dependency-start"])

        release.set()
        self.assertTrue(self._wait_until(
            lambda: dependent_handle.state == DONE,
        ))
        self.assertEqual(
            order,
            ["dependency-start", "dependency-done", "dependent"],
        )
        self.assertEqual(dependency_handle.result, "dep")
        self.assertEqual(dependent_handle.result, "result")

    def test_dependent_starts_after_on_done_side_effect(self):
        service = TaskService()
        release = threading.Event()
        started = threading.Event()
        order = []

        def dependency(_log):
            started.set()
            release.wait(3)
            return "dep"

        def dependent(_log):
            order.append("dependent")
            return "result"

        service.run(
            dependency,
            owner="demo",
            operation_key="demo.ordered",
            on_done=lambda _result, _error: order.append("done"),
        )
        service.run(
            dependent,
            owner="demo",
            operation_key="demo.after_ordered",
            depends_on=("demo.ordered",),
        )
        self.assertTrue(started.wait(2))
        self.assertEqual(order, [])

        release.set()
        self.assertTrue(self._wait_until(
            lambda: "dependent" in order,
        ))
        self.assertEqual(order, ["done", "dependent"])

    def test_cancel_operation_marks_cancelled(self):
        service = TaskService()
        started = threading.Event()
        release = threading.Event()
        done = []

        def work(_log):
            started.set()
            release.wait(3)
            return 1

        handle = service.run(
            work,
            owner="demo",
            operation_key="demo.cancel",
            on_done=lambda result, error: done.append((result, error)),
        )
        self.assertTrue(started.wait(2))
        self.assertTrue(service.cancel_operation("demo.cancel"))
        release.set()

        self.assertTrue(self._wait_until(
            lambda: handle.state == CANCELLED,
        ))
        self.assertEqual(handle.state, CANCELLED)
        self.assertIsNone(handle.result)
        self.assertTrue(done)
        self.assertIsInstance(done[0][1], RuntimeError)

    def test_failed_dependency_cancels_dependent(self):
        service = TaskService()
        started = threading.Event()
        release = threading.Event()

        def dependency(_log):
            started.set()
            release.wait(3)
            raise RuntimeError("boom")

        def dependent(_log):
            return "unexpected"

        service.run(
            dependency,
            owner="demo",
            operation_key="demo.failed",
        )
        self.assertTrue(started.wait(2))
        dependent_handle = service.run(
            dependent,
            owner="demo",
            operation_key="demo.blocked",
            depends_on=("demo.failed",),
        )
        self.assertEqual(dependent_handle.state, QUEUED)

        release.set()
        self.assertTrue(self._wait_until(
            lambda: dependent_handle.state == FAILED,
        ))
        self.assertIn("demo.failed", dependent_handle.error)

    def test_dependency_completion_during_check_is_not_lost(self):
        service = _BarrierTaskService()
        self.addCleanup(service.shutdown, 1000)
        service.operations.upsert_external(
            "vfs.prebuild",
            owner="<platform>",
            title="Prebuild",
            state=RUNNING,
        )

        def finish_dependency():
            self.assertTrue(service.check_entered.wait(2))
            service.operations.upsert_external(
                "vfs.prebuild",
                owner="<platform>",
                title="Prebuild",
                state=DONE,
            )
            service.check_release.set()

        helper = threading.Thread(target=finish_dependency, daemon=True)
        helper.start()
        handle = service.run(
            lambda _log: "ok",
            owner="demo",
            operation_key="demo.after_check",
            depends_on=("vfs.prebuild",),
        )
        helper.join(2)
        self.assertTrue(
            self._wait_until(lambda: handle.state == DONE),
            "зависимая задача осталась в queued после завершения зависимости",
        )
        self.assertEqual(handle.result, "ok")

    def test_external_completion_from_worker_runs_on_done_in_ui(self):
        service = TaskService()
        self.addCleanup(service.shutdown, 1000)
        service.operations.upsert_external(
            "vfs.prebuild",
            owner="<platform>",
            title="Prebuild",
            state=RUNNING,
        )
        done = []
        callback_threads = []

        def on_done(result, error):
            callback_threads.append(threading.current_thread())
            done.append((result, error))

        handle = service.run(
            lambda _log: "ok",
            owner="demo",
            operation_key="demo.after_worker_completion",
            depends_on=("vfs.prebuild",),
            on_done=on_done,
        )
        self.assertEqual(handle.state, QUEUED)

        def finish_dependency():
            service.operations.upsert_external(
                "vfs.prebuild",
                owner="<platform>",
                title="Prebuild",
                state=DONE,
            )

        helper = threading.Thread(target=finish_dependency)
        helper.start()
        helper.join(2)
        self.assertTrue(self._wait_until(lambda: done))
        self.assertEqual(done, [("ok", None)])
        self.assertTrue(
            all(
                thread is threading.main_thread()
                for thread in callback_threads
            )
        )

    def test_depends_on_callable_is_evaluated_at_enqueue(self):
        service = TaskService()
        self.addCleanup(service.shutdown, 1000)
        service.operations.upsert_external(
            "vfs.prebuild",
            owner="<platform>",
            title="Prebuild",
            state=RUNNING,
        )
        called = []

        def dependency():
            called.append(True)
            return ("vfs.prebuild",)

        handle = service.run(
            lambda _log: "ok",
            owner="demo",
            operation_key="demo.callable",
            depends_on=dependency,
        )
        self.assertEqual(called, [True])
        self.assertEqual(handle.state, QUEUED)

        service.operations.upsert_external(
            "vfs.prebuild",
            owner="<platform>",
            title="Prebuild",
            state=DONE,
        )
        self.assertTrue(self._wait_until(lambda: handle.is_finished))
        self.assertEqual(handle.result, "ok")

    def test_external_operation_completion_wakes_dependent(self):
        service = TaskService()
        service.operations.upsert_external(
            "vfs.prebuild",
            owner="<platform>",
            title="Prebuild",
            state=RUNNING,
        )
        handle = service.run(
            lambda _log: "ok",
            owner="demo",
            operation_key="demo.after_prebuild",
            depends_on=("vfs.prebuild",),
        )
        self.assertEqual(handle.state, QUEUED)

        service.operations.upsert_external(
            "vfs.prebuild",
            owner="<platform>",
            title="Prebuild",
            state=DONE,
        )
        self.assertTrue(self._wait_until(lambda: handle.state == DONE))
        self.assertEqual(handle.result, "ok")

    def test_external_operation_failure_fails_dependent(self):
        service = TaskService()
        service.operations.upsert_external(
            "vfs.prebuild",
            owner="<platform>",
            title="Prebuild",
            state=RUNNING,
        )
        handle = service.run(
            lambda _log: "unexpected",
            owner="demo",
            operation_key="demo.after_failed_prebuild",
            depends_on=("vfs.prebuild",),
        )
        self.assertEqual(handle.state, QUEUED)

        service.operations.upsert_external(
            "vfs.prebuild",
            owner="<platform>",
            title="Prebuild",
            state=FAILED,
            error="prebuild failed",
        )
        self.assertTrue(self._wait_until(lambda: handle.state == FAILED))
        self.assertIn("vfs.prebuild", handle.error)

    def test_trimmed_failed_dependency_still_fails_dependent(self):
        registry = OperationRegistry(max_finished=1)
        service = TaskService(operations=registry)
        registry.upsert_external(
            "vfs.prebuild",
            owner="<platform>",
            title="Prebuild",
            state=RUNNING,
        )
        registry.upsert_external(
            "vfs.prebuild",
            owner="<platform>",
            title="Prebuild",
            state=FAILED,
            error="prebuild failed",
        )
        registry.upsert_external(
            "other",
            owner="demo",
            title="Other",
            state=DONE,
        )
        self.assertIsNone(registry.get("vfs.prebuild"))
        self.assertEqual(registry.last_state("vfs.prebuild"), FAILED)

        handle = service.run(
            lambda _log: "unexpected",
            owner="demo",
            operation_key="demo.after_trimmed",
            depends_on=("vfs.prebuild",),
        )
        self.assertEqual(handle.state, FAILED)
        self.assertIn("vfs.prebuild", handle.error)

    def test_operation_key_is_namespaced_by_owner(self):
        service = TaskService()
        release = threading.Event()
        first = service.run(
            lambda _log: release.wait(3),
            owner="mod_a",
            operation_key="items.catalog",
        )
        second = service.run(
            lambda _log: release.wait(3),
            owner="mod_b",
            operation_key="items.catalog",
        )

        try:
            self.assertIsNot(first, second)
            self.assertEqual(first.key, "mod_a:items.catalog")
            self.assertEqual(second.key, "mod_b:items.catalog")
            self.assertTrue(service.operations.is_active(first.key))
            self.assertTrue(service.operations.is_active(second.key))
        finally:
            release.set()
            self.assertTrue(self._wait_finished(first, second))

    def test_pool_limits_concurrent_tasks(self):
        service = TaskService(max_workers=2)
        self.addCleanup(service.shutdown, 1000)
        release = threading.Event()
        lock = threading.Lock()
        started = []

        def work(_log):
            with lock:
                started.append(threading.current_thread().name)
            release.wait(3)
            return 1

        handles = [
            service.run(
                work,
                owner="demo",
                operation_key="demo.pool-%d" % index,
            )
            for index in range(4)
        ]
        self.assertTrue(self._wait_until(lambda: len(started) == 2))
        self.assertEqual(len(started), 2)

        release.set()
        self.assertTrue(self._wait_finished(*handles))

    def test_priority_orders_queued_tasks(self):
        service = TaskService(max_workers=1)
        self.addCleanup(service.shutdown, 1000)
        blocker_started = threading.Event()
        release = threading.Event()
        lock = threading.Lock()
        order = []

        def blocker(_log):
            blocker_started.set()
            release.wait(3)
            return "blocker"

        def make_work(name):
            def work(_log):
                with lock:
                    order.append(name)
                return name

            return work

        blocker_handle = service.run(
            blocker,
            owner="demo",
            operation_key="demo.blocker",
        )
        self.assertTrue(blocker_started.wait(2))
        low = service.run(
            make_work("low"),
            owner="demo",
            operation_key="demo.low",
            priority=1,
        )
        high = service.run(
            make_work("high"),
            owner="demo",
            operation_key="demo.high",
            priority=10,
        )
        self.assertEqual(low.state, QUEUED)
        self.assertEqual(high.state, QUEUED)

        release.set()
        self.assertTrue(
            self._wait_until(lambda: order == ["high", "low"])
        )
        self.assertTrue(
            self._wait_finished(blocker_handle, low, high)
        )

    def test_cancelled_queued_task_does_not_run(self):
        service = TaskService(max_workers=1)
        self.addCleanup(service.shutdown, 1000)
        blocker_started = threading.Event()
        release = threading.Event()
        calls = []

        def blocker(_log):
            blocker_started.set()
            release.wait(3)
            return "blocker"

        blocker_handle = service.run(
            blocker,
            owner="demo",
            operation_key="demo.blocker",
        )
        self.assertTrue(blocker_started.wait(2))
        queued = service.run(
            lambda _log: calls.append("ran"),
            owner="demo",
            operation_key="demo.queued",
        )
        self.assertEqual(queued.state, QUEUED)

        queued.cancel()
        self.assertEqual(queued.state, CANCELLED)
        release.set()
        self.assertTrue(
            self._wait_finished(blocker_handle, queued)
        )
        self.assertEqual(calls, [])

    def test_current_task_is_visible_inside_worker(self):
        service = TaskService(max_workers=1)
        self.addCleanup(service.shutdown, 1000)
        seen = []

        def work(_log):
            handle = current_task()
            seen.append(handle.key if handle is not None else "")
            return 1

        handle = service.run(
            work,
            owner="demo",
            operation_key="demo.current",
        )
        self.assertTrue(
            self._wait_until(lambda: handle.is_finished)
        )
        self.assertEqual(seen, [handle.key])

    def test_self_dependency_is_rejected(self):
        service = TaskService()
        with self.assertRaises(ValueError):
            service.run(
                lambda _log: None,
                owner="demo",
                operation_key="demo.self",
                depends_on=("demo.self",),
            )

    def test_unknown_dependency_is_rejected(self):
        service = TaskService()
        with self.assertRaises(ValueError):
            service.run(
                lambda _log: None,
                owner="demo",
                operation_key="demo.unknown",
                depends_on=("missing.key",),
            )

    def test_shutdown_rejects_new_tasks(self):
        service = TaskService()
        self.assertTrue(service.shutdown(timeout_ms=100))
        with self.assertRaises(RuntimeError):
            service.run(lambda _log: None, owner="demo")


if __name__ == "__main__":
    unittest.main()
