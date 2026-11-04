# -*- coding: utf-8 -*-
import tempfile
import time
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.prebuild_service import PrebuildService


class FakeAdapter:
    resolver_script = Path("Resolver4.py")


class FakeResolver:
    def __init__(self, cached):
        self.cached = set(cached)
        self.calls = []
        self.adapter = FakeAdapter()

    def _key(self, path):
        return str(path)

    def resolved_key(self, path, forest, config_dir):
        return self._key(path)

    def has_resolved_key(self, key):
        return key in self.cached

    def has_resolved(self, path, forest, config_dir):
        return self._key(path) in self.cached

    def get_adapter(self):
        return self.adapter

    def ensure_adapter(self, forest, config_dir):
        return self.adapter

    def source_maps(self, forest):
        return {}, {}

    def assemble_resolved(self, path, forest, config_dir, log=None, key=None):
        path = str(path)
        self.calls.append(path)
        value = ("text:%s" % Path(path).name, [])
        self.cached.add(key if key is not None else path)
        return value

    def store_resolved(self, path, forest, config_dir, value, key=None):
        self.cached.add(key if key is not None else str(path))


class FakeForest:
    def __init__(self, roots, xml_files=()):
        self.roots = roots
        self.files = roots
        self.xml_files = list(xml_files)
        self.display_children = {}


class FakeXmlResolver:
    def __init__(self, cached=()):
        self.cached = set(cached)
        self.calls = []

    def refresh(self):
        pass

    def cache_key(self, path, config_dir, dxml=True):
        return "xml:" + str(path)

    def has_resolved_by_key(self, key):
        return key in self.cached

    def resolve_output(self, path, config_dir, log=None, key=None):
        self.calls.append(str(path))
        key = key or self.cache_key(path, config_dir)
        self.cached.add(key)
        return ("xml-text:%s" % Path(path).name, ())

    def store_output_by_key(self, key, value):
        self.cached.add(key)


class PrebuildServiceTest(unittest.TestCase):
    def test_skips_cached_and_collects_missing(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d)
            a = cfg / "a.ltx"
            b = cfg / "b.ltx"
            a.write_text("[a]\n", encoding="utf-8")
            b.write_text("[b]\n", encoding="utf-8")

            resolver = FakeResolver(cached=[str(a)])
            service = PrebuildService(resolver, workers=1)
            service.start(FakeForest([a, b]), cfg)
            service.wait()

            status = service.status()
            self.assertEqual(status["state"], "done")
            self.assertEqual(status["total"], 2)
            self.assertEqual(status["done"], 2)
            self.assertEqual(resolver.calls, [str(b)])

    def test_includes_xml_files(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d)
            a = cfg / "a.ltx"
            a.write_text("[a]\n", encoding="utf-8")
            xml = cfg / "menu.xml"
            xml.write_text("<menu/>\n", encoding="utf-8")

            resolver = FakeResolver(cached=[])
            xml_resolver = FakeXmlResolver()
            service = PrebuildService(
                resolver,
                xml_resolver=xml_resolver,
                workers=1,
            )
            service.start(
                FakeForest([a], xml_files=[xml]),
                cfg,
            )
            service.wait()

            status = service.status()
            self.assertEqual(status["state"], "done")
            self.assertEqual(status["total"], 2)
            self.assertEqual(status["done"], 2)
            self.assertEqual(xml_resolver.calls, [str(xml)])

    def test_skips_cached_xml(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d)
            xml = cfg / "menu.xml"
            xml.write_text("<menu/>\n", encoding="utf-8")

            xml_resolver = FakeXmlResolver(
                cached=["xml:" + str(xml)],
            )
            service = PrebuildService(
                FakeResolver(cached=[]),
                xml_resolver=xml_resolver,
                workers=1,
            )
            service.start(FakeForest([], xml_files=[xml]), cfg)
            service.wait()

            self.assertEqual(service.status()["done"], 1)
            self.assertEqual(xml_resolver.calls, [])

    def test_stop(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d)
            paths = []
            for i in range(3):
                p = cfg / ("f%d.ltx" % i)
                p.write_text("[x]\n", encoding="utf-8")
                paths.append(p)

            resolver = FakeResolver(cached=[])
            service = PrebuildService(resolver, workers=1)
            service.start(FakeForest(paths), cfg)
            service.stop()
            service.wait()
            self.assertIn(
                service.status()["state"],
                ("idle", "done"),
            )


if __name__ == "__main__":
    unittest.main()
