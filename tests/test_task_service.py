# -*- coding: utf-8 -*-
"""Тесты фоновых задач и доставки колбэков."""

import threading
import unittest

from tests.support import SRC  # noqa: F401
from tests.qt_support import qapp

from PyQt5.QtCore import QEventLoop, QTimer

from ui.tasks import TaskService


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
        self.assertTrue(service.wait_owner("demo", timeout_ms=3000))
        self.assertTrue(self._wait_until(
            lambda: not service.workers_for("demo"),
        ))

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

    def test_shutdown_reports_unfinished_task(self):
        service = TaskService()
        started = threading.Event()
        release = threading.Event()

        def work(_log):
            started.set()
            release.wait(3)

        service.run(work)
        self.assertTrue(started.wait(2))
        try:
            self.assertFalse(service.shutdown(timeout_ms=10))
        finally:
            release.set()
            self.assertTrue(service.wait_all(timeout_ms=3000))


if __name__ == "__main__":
    unittest.main()
