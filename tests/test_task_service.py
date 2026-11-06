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


if __name__ == "__main__":
    unittest.main()
