# -*- coding: utf-8 -*-
"""Координация сброса кеша и перезапуска предсборки."""

import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.facade import CoreServices


class _Cache:
    def clear(self):
        pass


class _Resolver:
    def invalidate_cache(self):
        pass


class _Xml:
    def clear(self):
        pass


class _Items:
    def clear_memory(self):
        pass


class _Prebuild:
    def __init__(self):
        self.state = "done"
        self.starts = []

    def status(self):
        return {"state": self.state}

    def start(self, forest, config_dir, overlay=None):
        self.starts.append((forest, config_dir, overlay))
        self.state = "running"


class PrebuildCoordinationTest(unittest.TestCase):
    def _services(self):
        services = CoreServices.__new__(CoreServices)
        services.cache = _Cache()
        services.resolver = _Resolver()
        services.xml = _Xml()
        services.items = _Items()
        services.item_icons = _Items()
        services.localization = _Items()
        services.prebuild = _Prebuild()
        services._prebuild_forest = object()
        services._prebuild_config_dir = Path("configs")
        services._prebuild_overlay = {}
        services._prebuild_stale = False
        return services

    def test_invalidate_marks_prebuild_stale(self):
        services = self._services()
        services.invalidate_cache()
        self.assertTrue(services._prebuild_stale)

    def test_resolver_invalidation_marks_prebuild_stale(self):
        services = self._services()
        services.invalidate_resolver_cache()
        self.assertTrue(services._prebuild_stale)

    def test_ensure_starts_prebuild_after_invalidation(self):
        services = self._services()
        services.invalidate_cache()

        self.assertTrue(services.ensure_prebuild())
        self.assertEqual(len(services.prebuild.starts), 1)
        self.assertFalse(services._prebuild_stale)

        # Пока сборка идёт и кеш не менялся, второй запуск не нужен.
        self.assertFalse(services.ensure_prebuild())
        self.assertEqual(len(services.prebuild.starts), 1)

    def test_ensure_restarts_running_prebuild_when_stale(self):
        services = self._services()
        services.invalidate_cache()
        services.ensure_prebuild()
        self.assertEqual(len(services.prebuild.starts), 1)

        services.invalidate_cache()
        self.assertTrue(services.ensure_prebuild())
        self.assertEqual(len(services.prebuild.starts), 2)


if __name__ == "__main__":
    unittest.main()
