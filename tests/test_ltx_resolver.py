# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.file_cache import FileCacheSystem
from core.models import LineSource
from core.prebuild_service import PrebuildService
from core.resolved_cache import ResolvedCache
from core.resolver_service import ResolverService
from core.tree_builder import TreeBuilder


class LtxResolverTest(unittest.TestCase):
    def test_assemble_resolved_returns_line_sources(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "gamedata" / "configs"
            cfg.mkdir(parents=True)
            (cfg / "system.ltx").write_text(
                '[system]\n#include "a.ltx"\n',
                encoding="cp1251",
            )
            (cfg / "a.ltx").write_text(
                '[test]\nx=1\n',
                encoding="cp1251",
            )

            cache = FileCacheSystem()
            forest = TreeBuilder(cache=cache).build(
                cfg,
                cfg / "system.ltx",
                use_cache=False,
            )
            service = ResolverService(
                cache=cache,
                resolved_cache=ResolvedCache(),
            )

            text, sources = service.assemble_resolved(
                cfg / "system.ltx",
                forest,
                cfg,
            )
            self.assertIn("[test]", text)
            self.assertEqual(
                len(sources),
                len(text.split("\n")),
            )
            self.assertTrue(
                any(
                    isinstance(src, LineSource) and src.kind in ("original", "file")
                    for src in sources
                    if src is not None
                )
            )

            _, sources2 = service.assemble_resolved(
                cfg / "system.ltx",
                forest,
                cfg,
            )
            self.assertEqual(sources, sources2)
            self.assertEqual(service.cache_stats()["hits"], 1)

    def test_prebuild_result_is_used_on_open(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "gamedata" / "configs"
            cfg.mkdir(parents=True)
            (cfg / "system.ltx").write_text(
                '[system]\n#include "a.ltx"\n',
                encoding="cp1251",
            )
            (cfg / "a.ltx").write_text(
                '[test]\nx=1\n',
                encoding="cp1251",
            )

            cache = FileCacheSystem()
            forest = TreeBuilder(cache=cache).build(
                cfg,
                cfg / "system.ltx",
                use_cache=False,
            )
            service = ResolverService(
                cache=cache,
                resolved_cache=ResolvedCache(),
            )
            prebuild = PrebuildService(service, workers=1)
            prebuild.start(forest, cfg)
            prebuild.wait()

            self.assertEqual(prebuild.status()["done"], 1)
            misses_after_prebuild = service.cache_stats()["misses"]

            text, _sources = service.assemble_resolved(
                cfg / "system.ltx",
                forest,
                cfg,
            )

            self.assertIn("[test]", text)
            self.assertEqual(
                service.cache_stats()["misses"],
                misses_after_prebuild,
            )
            self.assertGreaterEqual(service.cache_stats()["hits"], 1)


if __name__ == "__main__":
    unittest.main()
