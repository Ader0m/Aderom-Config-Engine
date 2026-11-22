# -*- coding: utf-8 -*-
"""CacheService: снимок, single-flight, зависимости и версия формата."""

import tempfile
import threading
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.cache.service import CacheService
from core.resolver.service import ResolverService
from core.tools.cache import FileCacheSystem, ResolvedCache


class CacheServiceTest(unittest.TestCase):
    def test_get_or_compute_computes_once(self):
        cache = CacheService()
        calls = []

        def compute():
            calls.append(1)
            return "value"

        self.assertEqual(
            cache.get_or_compute("query", "key", compute, snapshot_id="s1"),
            "value",
        )
        self.assertEqual(
            cache.get_or_compute("query", "key", compute, snapshot_id="s1"),
            "value",
        )
        self.assertEqual(calls, [1])
        stats = cache.stats()["namespaces"]["query"]
        self.assertEqual(stats["computes"], 1)
        self.assertEqual(stats["hits"], 1)

    def test_snapshot_isolation(self):
        cache = CacheService()
        calls = []

        def compute(value):
            def inner():
                calls.append(value)
                return value

            return inner

        self.assertEqual(
            cache.get_or_compute(
                "tree",
                "root",
                compute("a"),
                snapshot_id="snapshot-a",
            ),
            "a",
        )
        self.assertEqual(
            cache.get_or_compute(
                "tree",
                "root",
                compute("b"),
                snapshot_id="snapshot-b",
            ),
            "b",
        )
        self.assertEqual(calls, ["a", "b"])

    def test_single_flight_computes_once_for_concurrent_callers(self):
        cache = CacheService()
        started = threading.Event()
        release = threading.Event()
        calls = []
        results = []

        def compute():
            calls.append(1)
            started.set()
            release.wait(5)
            return "value"

        def worker():
            results.append(
                cache.get_or_compute(
                    "tree",
                    "shared",
                    compute,
                    snapshot_id="s1",
                )
            )

        first = threading.Thread(target=worker)
        second = threading.Thread(target=worker)
        first.start()
        second.start()
        self.assertTrue(started.wait(5))
        release.set()
        first.join(5)
        second.join(5)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(calls, [1])
        self.assertEqual(results, ["value", "value"])

    def test_dependency_invalidation(self):
        cache = CacheService()
        calls = []
        root = Path("configs").resolve() / "system.ltx"

        def compute():
            calls.append(1)
            return "forest"

        cache.get_or_compute(
            "tree",
            "root",
            compute,
            deps=(root,),
            snapshot_id="s1",
        )
        cache.invalidate_deps([root])
        cache.get_or_compute(
            "tree",
            "root",
            compute,
            deps=(root,),
            snapshot_id="s1",
        )
        self.assertEqual(calls, [1, 1])

    def test_snapshot_invalidation(self):
        cache = CacheService()
        calls = []

        def compute():
            calls.append(1)
            return "value"

        cache.get_or_compute("tree", "key", compute, snapshot_id="s1")
        cache.invalidate_snapshot("s1")
        cache.get_or_compute("tree", "key", compute, snapshot_id="s1")
        self.assertEqual(calls, [1, 1])

    def test_resolver_targeted_invalidation_uses_dependencies(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "configs"
            cfg.mkdir()
            root = cfg / "system.ltx"
            root.write_text("[system]\nx = 1\n", encoding="cp1251")
            forest = type(
                "Forest",
                (),
                {
                    "cache_id": "snapshot-1",
                    "display_children": {},
                    "scan_dir": str(cfg),
                    "system_root": str(root),
                },
            )()

            cache = CacheService()
            service = ResolverService(
                FileCacheSystem(),
                ResolvedCache(),
                cache_service=cache,
            )
            key = service.resolved_key(root, forest, cfg)
            service.store_resolved(
                root,
                forest,
                cfg,
                ("text", ()),
                key=key,
            )
            self.assertTrue(service.has_resolved_key(key))

            service.invalidate_roots([root])
            self.assertFalse(service.has_resolved_key(key))

    def test_disk_cache_is_reused_without_memory_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            cache_dir = Path(d)
            first = CacheService(
                resolved_cache=ResolvedCache(cache_dir=cache_dir)
            )
            first.set("xml", ("key",), ("value", None))

            second = CacheService(
                resolved_cache=ResolvedCache(cache_dir=cache_dir)
            )
            calls = []

            def compute():
                calls.append(1)
                return ("other", None)

            self.assertEqual(
                second.get_or_compute("xml", ("key",), compute),
                ("value", None),
            )
            self.assertEqual(calls, [])

    def test_discard_removes_disk_entry(self):
        with tempfile.TemporaryDirectory() as d:
            cache_dir = Path(d)
            cache = ResolvedCache(cache_dir=cache_dir)
            cache.set("key", ("value", None))
            self.assertTrue(cache.has("key"))

            cache.discard(lambda key: key == "key")
            self.assertFalse(cache.has("key"))
            fresh = ResolvedCache(cache_dir=cache_dir)
            self.assertIsNone(fresh.get("key"))

    def test_cache_service_invalidation_removes_disk_entry(self):
        with tempfile.TemporaryDirectory() as d:
            cache_dir = Path(d)
            path = Path(d) / "system.ltx"
            first = CacheService(
                resolved_cache=ResolvedCache(cache_dir=cache_dir)
            )
            first.set(
                "xml",
                ("key",),
                ("value", None),
                deps=(path,),
            )
            first.invalidate_deps([path])

            second = CacheService(
                resolved_cache=ResolvedCache(cache_dir=cache_dir)
            )
            calls = []

            def compute():
                calls.append(1)
                return ("new", None)

            self.assertEqual(
                second.get_or_compute(
                    "xml",
                    ("key",),
                    compute,
                    deps=(path,),
                ),
                ("new", None),
            )
            self.assertEqual(calls, [1])

    def test_resolver_invalidate_roots_removes_disk_entry(self):
        with tempfile.TemporaryDirectory() as d:
            cache_dir = Path(d) / "cache"
            cfg = Path(d) / "configs"
            cfg.mkdir()
            root = cfg / "system.ltx"
            root.write_text("[system]\nx = 1\n", encoding="cp1251")
            forest = type(
                "Forest",
                (),
                {
                    "cache_id": "snapshot-1",
                    "display_children": {},
                    "scan_dir": str(cfg),
                    "system_root": str(root),
                },
            )()

            resolved = ResolvedCache(cache_dir=cache_dir)
            service = ResolverService(FileCacheSystem(), resolved)
            key = service.resolved_key(root, forest, cfg)
            service.store_resolved(
                root,
                forest,
                cfg,
                ("text", ()),
                key=key,
            )
            self.assertTrue(service.has_resolved_key(key))

            service.invalidate_roots([root])
            self.assertFalse(service.has_resolved_key(key))
            self.assertIsNone(
                ResolvedCache(cache_dir=cache_dir).get(key)
            )

    def test_disk_format_version_is_not_migrated(self):
        with tempfile.TemporaryDirectory() as d:
            old = ResolvedCache(cache_dir=Path(d), format_version=1)
            old.set("key", ("old", None))
            new = ResolvedCache(cache_dir=Path(d), format_version=2)
            self.assertIsNone(new.get("key"))


if __name__ == "__main__":
    unittest.main()
