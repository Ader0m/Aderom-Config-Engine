# -*- coding: utf-8 -*-
"""Очередь событий из фонового потока (backpressure)."""

import threading
import unittest

from tests.qt_support import qapp
from tests.support import SRC  # noqa: F401

from PyQt5.QtCore import QEventLoop, QTimer

from ui.bus import EventBus


class EventBackpressureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qapp()

    def _run_loop(self, delay=50):
        loop = QEventLoop()
        QTimer.singleShot(delay, loop.quit)
        loop.exec_()

    def test_background_publish_is_coalesced_into_one_queue(self):
        bus = EventBus(max_pending=100)
        received = []
        bus.subscribe("ui.bulk", lambda payload: received.append(payload))

        def worker():
            for index in range(3):
                bus.publish("ui.bulk", index)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(2)

        self.assertFalse(thread.is_alive())
        self.assertEqual(bus.pending_count, 3)
        self._run_loop()
        self.assertEqual(received, [0, 1, 2])
        self.assertEqual(bus.pending_count, 0)
        self.assertEqual(bus.dropped_events, 0)

    def test_bounded_queue_drops_oldest_events(self):
        bus = EventBus(max_pending=2)
        received = []
        bus.subscribe("ui.bulk", lambda payload: received.append(payload))

        def worker():
            for index in range(5):
                bus.publish("ui.bulk", index)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(2)

        self.assertEqual(bus.pending_count, 2)
        self.assertEqual(bus.dropped_events, 3)

        self._run_loop()
        self.assertEqual(received, [3, 4])
        self.assertEqual(bus.dropped_events, 3)

    def test_clear_pending_drops_scheduled_events(self):
        bus = EventBus(max_pending=100)
        received = []
        bus.subscribe("ui.bulk", lambda payload: received.append(payload))

        thread = threading.Thread(
            target=lambda: bus.publish("ui.bulk", 1)
        )
        thread.start()
        thread.join(2)
        self.assertEqual(bus.pending_count, 1)

        self.assertEqual(bus.clear_pending(), 1)
        self._run_loop()
        self.assertEqual(received, [])


if __name__ == "__main__":
    unittest.main()
