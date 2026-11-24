# -*- coding: utf-8 -*-
"""Фоновые каталоги подсказок и их отмена."""

import unittest
from types import SimpleNamespace

from tests.support import SRC  # noqa: F401

from ui.platform.hints import HintCatalogs


class _Handle:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class _Tasks:
    def __init__(self):
        self.calls = []

    def run(self, fn, on_done=None, owner=None, **kwargs):
        handle = _Handle()
        self.calls.append((fn, on_done, handle))
        return handle


class _Services:
    def __init__(self):
        self.calls = []

    def color_set(self, forest, overlay=None):
        self.calls.append("colors")
        return "COLORS"

    def include_set(self, forest, overlay=None):
        self.calls.append("includes")
        return "INCLUDES"


class HintCatalogsBackgroundTest(unittest.TestCase):
    def test_colors_and_includes_run_in_tasks_and_close_cancels(self):
        services = _Services()
        tasks = _Tasks()
        forest = SimpleNamespace(cache_id="gen-1")
        catalogs = HintCatalogs(
            services,
            lambda: forest,
            lambda: None,
            tasks=tasks,
        )

        catalogs.refresh_colors()
        catalogs.refresh_includes()

        self.assertEqual(services.calls, [])
        self.assertEqual(len(tasks.calls), 2)

        build, apply, handle = tasks.calls[0]
        self.assertEqual(build(), "COLORS")
        apply("COLORS", None)
        self.assertEqual(catalogs.colors, "COLORS")
        self.assertFalse(handle.cancelled)

        catalogs.close()
        self.assertFalse(handle.cancelled)
        self.assertTrue(tasks.calls[1][2].cancelled)
        self.assertEqual(catalogs._pending, set())


if __name__ == "__main__":
    unittest.main()
