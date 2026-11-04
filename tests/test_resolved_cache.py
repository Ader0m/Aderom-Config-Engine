# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.models import LineSource
from core.resolved_cache import (
    ResolvedCache,
    file_subtree_signature,
    invalidate_forest_signatures,
)


class FakeForest:
    def __init__(self, files):
        self.files = files
        self.display_children = {}


class ResolvedCacheTest(unittest.TestCase):
    def test_memory_hit(self):
        cache = ResolvedCache()
        value = ("text", [LineSource(kind="original")])
        cache.set(("key",), value)

        self.assertTrue(cache.has(("key",)))
        self.assertEqual(cache.get(("key",)), value)
        self.assertEqual(cache.stats()["hits"], 1)

    def test_disk_hit_survives_new_instance(self):
        with tempfile.TemporaryDirectory() as d:
            cache_dir = Path(d) / "cache"
            value = ("text-1", [LineSource(kind="file", display="a.ltx")])

            first = ResolvedCache(cache_dir=cache_dir)
            first.set(("root", 1), value)

            second = ResolvedCache(cache_dir=cache_dir)
            self.assertTrue(second.has(("root", 1)))
            self.assertEqual(second.get(("root", 1)), value)
            self.assertEqual(second.stats()["disk_hits"], 1)

    def test_lru_limit(self):
        cache = ResolvedCache(max_entries=2)
        cache.set("a", ("a", None))
        cache.set("b", ("b", None))
        cache.set("c", ("c", None))

        self.assertIsNone(cache.get("a"))
        self.assertIsNotNone(cache.get("b"))
        self.assertIsNotNone(cache.get("c"))

    def test_signature_changes_after_external_edit(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "a.ltx"
            path.write_text("[a]\nx=1\n", encoding="utf-8")

            forest = FakeForest([path])
            before = file_subtree_signature(forest, path)

            path.write_text("[a]\nx=2\n", encoding="utf-8")
            stat = path.stat()
            os.utime(
                path,
                ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000),
            )

            # Подпись читает mtime/size/ctime заново.
            after = file_subtree_signature(forest, path)

            self.assertNotEqual(before, after)

    def test_invalidate_signatures(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "a.ltx"
            path.write_text("[a]\n", encoding="utf-8")
            forest = FakeForest([path])

            first = file_subtree_signature(forest, path)
            invalidate_forest_signatures(forest)
            second = file_subtree_signature(forest, path)

            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
