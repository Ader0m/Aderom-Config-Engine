# -*- coding: utf-8 -*-
"""Координация сброса кеша и перезапуска предсборки."""

import threading
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.services.cache_control import CacheControl
from core.services.prebuild import PrebuildServices
from types import SimpleNamespace

class _Resolver:
    def invalidate_cache(self):
        pass

    def invalidate_roots(self, roots):
        pass


class _Xml:
    def clear(self):
        pass


class _Items:
    def clear_memory(self):
        pass


class _Graph:
    def clear_cache(self):
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
        prebuild = PrebuildServices(
            None,
            None,
            service=_Prebuild(),
        )
        prebuild._forest = object()
        prebuild._config_dir = Path("configs")
        control = CacheControl(
            cache_service=None,
            resolve=_Resolver(),
            graph=_Graph(),
            data=_Items(),
            prebuild=prebuild,
        )
        return SimpleNamespace(prebuild=prebuild, cache_control=control)

    def test_invalidate_marks_prebuild_stale(self):
        services = self._services()
        services.cache_control.invalidate_cache()
        self.assertTrue(services.prebuild._stale)

    def test_resolver_invalidation_marks_prebuild_stale(self):
        services = self._services()
        services.cache_control.invalidate_resolver_cache()
        self.assertTrue(services.prebuild._stale)

    def test_ensure_starts_prebuild_after_invalidation(self):
        services = self._services()
        services.cache_control.invalidate_cache()

        self.assertTrue(services.prebuild.ensure())
        self.assertEqual(len(services.prebuild.prebuild.starts), 1)
        self.assertFalse(services.prebuild._stale)

        # Пока сборка идёт и кеш не менялся, второй запуск не нужен.
        self.assertFalse(services.prebuild.ensure())
        self.assertEqual(len(services.prebuild.prebuild.starts), 1)

    def test_invalidate_paths_prebuilds_only_affected_roots(self):
        services = self._services()
        root = Path("configs") / "system.ltx"

        services.cache_control.invalidate_paths({root})
        self.assertTrue(services.prebuild.ensure())

        self.assertEqual(len(services.prebuild.prebuild.starts), 1)
        self.assertEqual(
            services.prebuild.prebuild.starts[0][3],
            {root},
        )

    def test_ensure_restarts_running_prebuild_when_stale(self):
        services = self._services()
        services.cache_control.invalidate_cache()
        services.prebuild.ensure()
        self.assertEqual(len(services.prebuild.prebuild.starts), 1)

        services.cache_control.invalidate_cache()
        self.assertTrue(services.prebuild.ensure())
        self.assertEqual(len(services.prebuild.prebuild.starts), 2)

    def test_concurrent_ensure_queues_only_one_build(self):
        services = self._services()
        services.cache_control.invalidate_cache()

        barrier = threading.Barrier(2)
        results = []

        def worker():
            barrier.wait()
            results.append(services.prebuild.ensure())

        threads = [
            threading.Thread(target=worker),
            threading.Thread(target=worker),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(2.0)

        self.assertEqual(sum(1 for value in results if value), 1)
        self.assertEqual(len(services.prebuild.prebuild.starts), 1)
        self.assertFalse(services.prebuild._stale)


if __name__ == "__main__":
    unittest.main()
