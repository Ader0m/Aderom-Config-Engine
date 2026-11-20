# -*- coding: utf-8 -*-
"""Координация сброса кеша и перезапуска предсборки."""

import threading
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

    def start(self, forest, config_dir, overlay=None, roots=None):
        self.starts.append((forest, config_dir, overlay, roots))
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
        services._prebuild_lock = threading.RLock()
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

    def test_invalidate_paths_prebuilds_only_affected_roots(self):
        services = self._services()
        root = Path("configs") / "system.ltx"

        services.invalidate_paths({root})
        self.assertTrue(services.ensure_prebuild())

        self.assertEqual(len(services.prebuild.starts), 1)
        self.assertEqual(
            services.prebuild.starts[0][3],
            {root},
        )

    def test_ensure_restarts_running_prebuild_when_stale(self):
        services = self._services()
        services.invalidate_cache()
        services.ensure_prebuild()
        self.assertEqual(len(services.prebuild.starts), 1)

        services.invalidate_cache()
        self.assertTrue(services.ensure_prebuild())
        self.assertEqual(len(services.prebuild.starts), 2)

    def test_concurrent_ensure_queues_only_one_build(self):
        services = self._services()
        services.invalidate_cache()

        barrier = threading.Barrier(2)
        results = []

        def worker():
            barrier.wait()
            results.append(services.ensure_prebuild())

        threads = [
            threading.Thread(target=worker),
            threading.Thread(target=worker),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(2.0)

        self.assertEqual(sum(1 for value in results if value), 1)
        self.assertEqual(len(services.prebuild.starts), 1)
        self.assertFalse(services._prebuild_stale)


if __name__ == "__main__":
    unittest.main()
