# -*- coding: utf-8 -*-
"""CACHE-3: CacheService — единственный владелец кэшей в production."""

import ast
import inspect
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.support import SRC  # noqa: F401
from tests.helpers import write_text

from core.cache.service import CacheService
from core.composition import CoreComposition
from core.resolver.service import ResolverService
from core.tree.builder import TreeBuilder
from core.xml.service import XmlResolveService


class CacheOwnershipTest(unittest.TestCase):
    def test_configured_resolved_backend_is_owned_by_cache_service(self):
        with tempfile.TemporaryDirectory() as d:
            cache_dir = Path(d) / "resolved"
            first = CacheService(cache_dir=cache_dir, format_version=2)
            first.set(
                "resolve.text",
                ("key",),
                ("text", ()),
                snapshot_id="snapshot-1",
            )

            second = CacheService(cache_dir=cache_dir, format_version=2)
            self.assertEqual(
                second.get(
                    "resolve.text",
                    ("key",),
                    snapshot_id="snapshot-1",
                ),
                ("text", ()),
            )
            self.assertIsNotNone(second._resolved)
            self.assertIsNone(second._memory._cache_dir)

    def test_tree_builder_with_cache_service_has_no_fallback_cache(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "configs"
            root = write_text(cfg / "system.ltx", "[system]\nx = 1\n")
            cache = CacheService()
            builder = TreeBuilder(cache_service=cache)

            self.assertIsNone(builder.cache)
            builder.build(cfg, root, use_cache=True, snapshot_id="s")

            key = builder.cache_key(cfg, root, None, "s")
            self.assertTrue(cache.has("tree", key, snapshot_id="s"))

    def test_resolver_with_cache_service_has_no_fallback_cache(self):
        cache = CacheService()
        service = ResolverService(cache_service=cache)

        self.assertIsNone(service._resolved_cache)
        service.invalidate_cache()
        key = ("resolved", "root.ltx", "snapshot-1")
        service.store_resolved(
            Path("root.ltx"),
            None,
            Path("configs"),
            ("text", ()),
            key=key,
        )
        self.assertTrue(
            cache.has(
                "resolve.text",
                key,
                snapshot_id="snapshot-1",
            )
        )

    def test_resolver_invalidate_cache_clears_cache_service_namespaces(self):
        cache = CacheService()
        service = ResolverService(cache_service=cache)
        key = ("resolved", "root.ltx", "snapshot-1")
        service.store_resolved(
            Path("root.ltx"),
            None,
            Path("configs"),
            ("text", ()),
            key=key,
        )
        cache.set(
            "resolve.semantic",
            ("semantic",),
            object(),
            snapshot_id="snapshot-1",
        )

        self.assertTrue(
            cache.has("resolve.text", key, snapshot_id="snapshot-1")
        )
        self.assertTrue(
            cache.has(
                "resolve.semantic",
                ("semantic",),
                snapshot_id="snapshot-1",
            )
        )

        service.invalidate_cache()

        self.assertFalse(
            cache.has("resolve.text", key, snapshot_id="snapshot-1")
        )
        self.assertFalse(
            cache.has(
                "resolve.semantic",
                ("semantic",),
                snapshot_id="snapshot-1",
            )
        )

    def test_core_services_invalidate_resolver_cache_clears_namespaces(self):
        from core.services.cache_control import CacheControl

        cache = CacheService()
        resolver = ResolverService(cache_service=cache)
        control = CacheControl(
            cache_service=cache,
            resolve=resolver,
            graph=SimpleNamespace(clear_cache=lambda: None),
            data=SimpleNamespace(clear_memory=lambda: None),
            prebuild=SimpleNamespace(mark_stale=lambda roots=None: None),
        )

        key = ("resolved", "root.ltx", "snapshot-1")
        resolver.store_resolved(
            Path("root.ltx"),
            None,
            Path("configs"),
            ("text", ()),
            key=key,
        )
        cache.set(
            "resolve.semantic",
            ("semantic",),
            object(),
            snapshot_id="snapshot-1",
        )

        control.invalidate_resolver_cache()

        self.assertFalse(
            cache.has("resolve.text", key, snapshot_id="snapshot-1")
        )
        self.assertFalse(
            cache.has(
                "resolve.semantic",
                ("semantic",),
                snapshot_id="snapshot-1",
            )
        )

    def test_xml_service_with_cache_service_has_no_fallback_cache(self):
        cache = CacheService()
        service = XmlResolveService(cache_service=cache)
        key = ("xml", "config", "main.xml", "overlay", "scripts", True)

        self.assertIsNone(service._resolved_cache)
        service.store_output_by_key(key, "value")
        self.assertTrue(service.has_resolved_by_key(key))
        service.clear()
        self.assertFalse(service.has_resolved_by_key(key))

    def test_composition_root_has_one_cache_owner(self):
        tree = ast.parse((SRC / "main.py").read_text(encoding="utf-8-sig"))
        imported = set()
        cache_service_calls = []
        calls = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.update(alias.name for alias in node.names)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                calls.setdefault(node.func.id, []).append(node)
                if node.func.id == "CacheService":
                    cache_service_calls.append(node)

        self.assertNotIn("FileCacheSystem", imported)
        self.assertNotIn("ResolvedCache", imported)
        self.assertEqual(len(cache_service_calls), 1)
        keywords = {keyword.arg for keyword in cache_service_calls[0].keywords}
        self.assertIn("cache_dir", keywords)

        def call_keywords(name):
            self.assertEqual(len(calls.get(name, [])), 1, name)
            return {
                keyword.arg
                for keyword in calls[name][0].keywords
            }

        tree_keywords = call_keywords("TreeBuilder")
        self.assertIn("cache_service", tree_keywords)
        self.assertNotIn("cache", tree_keywords)

        resolver_keywords = call_keywords("ResolverService")
        self.assertIn("cache_service", resolver_keywords)
        self.assertNotIn("cache", resolver_keywords)
        self.assertNotIn("resolved_cache", resolver_keywords)

        xml_keywords = call_keywords("XmlResolveService")
        self.assertIn("cache_service", xml_keywords)
        self.assertNotIn("resolved_cache", xml_keywords)

        services_keywords = call_keywords("CoreComposition")
        self.assertIn("cache_service", services_keywords)
        self.assertNotIn("cache", services_keywords)

    def test_composition_has_no_legacy_cache_parameter(self):
        parameters = inspect.signature(CoreComposition).parameters
        self.assertNotIn("cache", parameters)
        self.assertIn("cache_service", parameters)


if __name__ == "__main__":
    unittest.main()
