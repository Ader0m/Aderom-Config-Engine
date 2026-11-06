# -*- coding: utf-8 -*-
"""Тесты маршрутизации открытия файлов."""

import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from sdk.contracts import FileOpenHandler
from ui.file_open_router import FileOpenRouter


class _Api:
    def __init__(self):
        self.logs = []

    def log(self, message):
        self.logs.append(message)


class _Handler(FileOpenHandler):
    def __init__(self, name, priority=0, can_open=True, result=True):
        self.name = name
        self.priority = priority
        self._can_open = can_open
        self._result = result
        self.calls = []

    def can_open(self, path):
        return self._can_open

    def open(self, path, api):
        self.calls.append(path)
        return self._result


class FileOpenRouterTest(unittest.TestCase):
    def setUp(self):
        self.api = _Api()

    def test_highest_priority_handler_wins(self):
        router = FileOpenRouter()
        low = _Handler("low", priority=1)
        high = _Handler("high", priority=10)
        router.register(low)
        router.register(high)

        self.assertTrue(router.open(Path("a.ltx"), self.api))
        self.assertEqual(low.calls, [])
        self.assertEqual(high.calls, [Path("a.ltx")])

    def test_handler_that_cannot_open_is_skipped(self):
        router = FileOpenRouter()
        refusing = _Handler("refusing", priority=100, can_open=False)
        accepting = _Handler("accepting", priority=0)
        router.register(refusing)
        router.register(accepting)

        self.assertTrue(router.open(Path("a.ltx"), self.api))
        self.assertEqual(refusing.calls, [])
        self.assertEqual(accepting.calls, [Path("a.ltx")])

    def test_selected_handler_result_does_not_fall_through(self):
        router = FileOpenRouter()
        first = _Handler("first", priority=10, result=False)
        second = _Handler("second", priority=0, result=True)
        router.register(first)
        router.register(second)

        # Маршрутизатор считает файл обработанным, если выбранный
        # обработчик согласился его открыть; false не запускает цепочку
        # заново. Фиксируем текущий контракт, чтобы не потерять его.
        self.assertTrue(router.open(Path("a.ltx"), self.api))
        self.assertEqual(first.calls, [Path("a.ltx")])
        self.assertEqual(second.calls, [])

    def test_default_handler_is_used_when_nobody_can_open(self):
        router = FileOpenRouter()
        handler = _Handler("handler", can_open=False)
        default = _Handler("default")
        router.register(handler)
        router.set_default(default)

        self.assertTrue(router.open(Path("a.ltx"), self.api))
        self.assertEqual(handler.calls, [])
        self.assertEqual(default.calls, [Path("a.ltx")])

    def test_no_handler_and_no_default_returns_false(self):
        router = FileOpenRouter()
        self.assertFalse(router.open(Path("a.ltx"), self.api))

    def test_open_default_bypasses_registered_handlers(self):
        router = FileOpenRouter()
        handler = _Handler("handler")
        default = _Handler("default")
        router.register(handler)
        router.set_default(default)

        self.assertTrue(router.open_default(Path("a.ltx"), self.api))
        self.assertEqual(handler.calls, [])
        self.assertEqual(default.calls, [Path("a.ltx")])


if __name__ == "__main__":
    unittest.main()
