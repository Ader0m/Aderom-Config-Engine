# -*- coding: utf-8 -*-
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support import SRC  # noqa: F401

from core import resolver_service as resolver_service_module
from core.file_cache import FileCacheSystem
from core.models import LineSource
from core.resolved_cache import ResolvedCache
from core.resolver_service import ResolverService


class FakeAdapter:
    resolver_script = Path("Resolver4.py")

    def __init__(self, delay=0.0):
        self.delay = delay
        self.calls = 0
        self.lock = threading.Lock()

    def assemble_resolved(self, root_path, source_map, abs_map):
        with self.lock:
            self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        return (
            "[test]\nx=1\n",
            [LineSource(kind="original")],
        )


class FakeForest:
    def __init__(self, path):
        self.files = [path]
        self.display_children = {}
        self.rel_paths = {path: path.name}


class ResolverServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self.tmp.name)
        self.path = self.cfg / "a.ltx"
        self.path.write_text("[test]\nx=1\n", encoding="utf-8")
        self.forest = FakeForest(self.path)
        self.adapter = FakeAdapter()
        self.service = ResolverService(
            cache=FileCacheSystem(),
            resolved_cache=ResolvedCache(),
        )
        self.service._adapter = self.adapter

    def tearDown(self):
        self.tmp.cleanup()

    def test_resolved_cache_hit(self):
        first = self.service.assemble_resolved(
            self.path, self.forest, self.cfg,
        )
        second = self.service.assemble_resolved(
            self.path, self.forest, self.cfg,
        )

        self.assertEqual(first, second)
        self.assertEqual(self.adapter.calls, 1)
        self.assertEqual(self.service.cache_stats()["hits"], 1)
        self.assertTrue(
            self.service.has_resolved(self.path, self.forest, self.cfg)
        )

    def test_inflight_deduplication(self):
        self.adapter.delay = 0.1
        results = []

        def work():
            results.append(
                self.service.assemble_resolved(
                    self.path, self.forest, self.cfg,
                )
            )

        threads = [threading.Thread(target=work) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.adapter.calls, 1)

    def test_key_is_stable_across_config_dir_and_path_case(self):
        key1 = self.service.resolved_key(self.path, self.forest, self.cfg)
        key2 = self.service.resolved_key(
            self.path,
            self.forest,
            self.cfg.parent,
        )
        self.assertEqual(key1, key2)

        if os.name == "nt":
            key3 = self.service.resolved_key(
                Path(str(self.path).upper()),
                self.forest,
                self.cfg,
            )
            self.assertEqual(key1, key3)

    def test_key_signature_is_computed_once(self):
        calls = []
        real = resolver_service_module.file_subtree_signature

        def counting(forest, root_path):
            calls.append(root_path)
            return real(forest, root_path)

        with patch.object(
            resolver_service_module,
            "file_subtree_signature",
            counting,
        ):
            self.service.assemble_resolved(
                self.path, self.forest, self.cfg,
            )
            self.service.assemble_resolved(
                self.path, self.forest, self.cfg,
            )

        self.assertEqual(calls, [self.path])
        self.assertEqual(self.adapter.calls, 1)

    def test_invalidate_cache_recomputes_key(self):
        calls = []
        real = resolver_service_module.file_subtree_signature

        def counting(forest, root_path):
            calls.append(root_path)
            return real(forest, root_path)

        with patch.object(
            resolver_service_module,
            "file_subtree_signature",
            counting,
        ):
            self.service.assemble_resolved(
                self.path, self.forest, self.cfg,
            )
            self.service.invalidate_cache()
            self.service.assemble_resolved(
                self.path, self.forest, self.cfg,
            )

        self.assertEqual(calls, [self.path, self.path])
        self.assertEqual(self.adapter.calls, 2)

    def test_store_resolved(self):
        value = ("stored", [LineSource(kind="original")])
        self.service.store_resolved(
            self.path, self.forest, self.cfg, value,
        )
        self.assertTrue(
            self.service.has_resolved(self.path, self.forest, self.cfg)
        )
        self.assertEqual(
            self.service.assemble_resolved(self.path, self.forest, self.cfg),
            value,
        )
        self.assertEqual(self.adapter.calls, 0)


if __name__ == "__main__":
    unittest.main()
