# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.tools.cache import FileCacheSystem
from core.models import LineSource
from core.tools.cache import (
    ResolvedCache,
    file_subtree_signature,
    invalidate_forest_signatures,
    resolver_signature,
)
from core.tree.builder import TreeBuilder
from tests.helpers import touch_after, write_text


class FakeForest:
    def __init__(self, files):
        self.files = files
        self.display_children = {}
        self.include_children = {}
        self.dltx_attachments = {}


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

    def test_lru_limit_keeps_recently_used_entry(self):
        cache = ResolvedCache(max_entries=2)
        cache.set("a", ("a", None))
        cache.set("b", ("b", None))

        # Обращение к "b" должно защитить его от вытеснения.
        self.assertIsNotNone(cache.get("b"))
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

    def test_invalidate_signatures_recomputes_same_value(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "a.ltx"
            path.write_text("[a]\n", encoding="utf-8")
            forest = FakeForest([path])

            first = file_subtree_signature(forest, path)
            invalidate_forest_signatures(forest)
            second = file_subtree_signature(forest, path)

            self.assertEqual(first, second)

    def test_include_dependency_change_invalidates_signature(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "gamedata" / "configs"
            root = write_text(
                cfg / "system.ltx",
                '[system]\n#include "a.ltx"\n',
            )
            include = write_text(cfg / "a.ltx", "[a]\nx = 1\n")
            forest = TreeBuilder(FileCacheSystem()).build(
                cfg,
                root,
                use_cache=False,
            )

            before = file_subtree_signature(forest, root)
            write_text(include, "[a]\nx = 2\n")
            touch_after(include)
            after = file_subtree_signature(forest, root)

            self.assertNotEqual(before, after)

    def test_resolver_script_signature_changes(self):
        with tempfile.TemporaryDirectory() as d:
            script = Path(d) / "Resolver4.py"
            script.write_text("resolver-v1\n", encoding="utf-8")
            before = resolver_signature(script)

            script.write_text("resolver-v2\n", encoding="utf-8")
            touch_after(script)
            after = resolver_signature(script)

            self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
