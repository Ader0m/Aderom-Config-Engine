# -*- coding: utf-8 -*-
import io
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.resolver.prebuild import PrebuildService


class FakeAdapter:
    resolver_script = Path("Resolver4.py")


class FakeResolver:
    def __init__(self, cached):
        self.cached = set(cached)
        self.calls = []
        self.adapter = FakeAdapter()

    def _key(self, path):
        return str(path)

    def resolved_key(self, path, forest, config_dir, overlay=None):
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

    def assemble_resolved(self, path, forest, config_dir, log=None, key=None, overlay=None):
        path = str(path)
        self.calls.append(path)
        value = ("text:%s" % Path(path).name, [])
        self.cached.add(key if key is not None else path)
        return value

    def store_resolved(self, path, forest, config_dir, value, key=None, overlay=None):
        self.cached.add(key if key is not None else str(path))


class BlockingResolver(FakeResolver):
    def __init__(self, cached=()):
        super().__init__(cached)
        self.entered = threading.Event()
        self.release = threading.Event()

    def assemble_resolved(self, path, forest, config_dir, log=None, key=None, overlay=None):
        path = str(path)
        self.calls.append(path)
        self.entered.set()
        if not self.release.wait(2.0):
            raise RuntimeError("резолвер не был отпущен")
        self.cached.add(key if key is not None else path)
        return ("text:%s" % Path(path).name, [])


class FailingResolver(FakeResolver):
    def assemble_resolved(self, path, forest, config_dir, log=None, key=None, overlay=None):
        self.calls.append(str(path))
        raise RuntimeError("boom")


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

    def cache_key(self, path, config_dir, dxml=True, overlay=None):
        return "xml:" + str(path)

    def has_resolved_by_key(self, key):
        return key in self.cached

    def resolve_output(self, path, config_dir, log=None, key=None,
                       overlay=None):
        self.calls.append(str(path))
        key = key or self.cache_key(
            path,
            config_dir,
            overlay=overlay,
        )
        self.cached.add(key)
        return ("xml-text:%s" % Path(path).name, ())

    def store_output_by_key(self, key, value):
        self.cached.add(key)


class OverlayRecordingResolver(FakeResolver):
    def __init__(self):
        super().__init__(cached=[])
        self.key_overlays = []
        self.value_overlays = []

    def overlay_for_root(self, forest, path, overlay):
        return overlay

    def resolved_key(self, path, forest, config_dir, overlay=None):
        self.key_overlays.append(overlay)
        return self._key(path)

    def assemble_resolved(
        self,
        path,
        forest,
        config_dir,
        log=None,
        key=None,
        overlay=None,
    ):
        self.value_overlays.append(overlay)
        return super().assemble_resolved(
            path,
            forest,
            config_dir,
            log=log,
            key=key,
            overlay=overlay,
        )


class PrebuildServiceTest(unittest.TestCase):
    def test_resolved_overlay_is_used_for_key_and_value(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d)
            path = cfg / "system.ltx"
            path.write_text("[system]\n", encoding="utf-8")

            resolver = OverlayRecordingResolver()
            service = PrebuildService(resolver, workers=1)
            overlay = {
                "source_files": {path: "[system]\n"},
                "resolved_edits": {
                    path: ("[system]\n", "[system]\nx = 1\n", ()),
                },
            }
            service.start(FakeForest([path]), cfg, overlay=overlay)
            service.wait()

            self.assertEqual(len(resolver.key_overlays), 1)
            self.assertEqual(len(resolver.value_overlays), 1)
            self.assertIs(
                resolver.key_overlays[0],
                resolver.value_overlays[0],
            )
            self.assertEqual(
                resolver.value_overlays[0]["resolved_edits"],
                overlay["resolved_edits"],
            )

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

    def test_roots_filter_collects_only_affected(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d)
            a = cfg / "a.ltx"
            b = cfg / "b.ltx"
            a.write_text("[a]\n", encoding="utf-8")
            b.write_text("[b]\n", encoding="utf-8")

            resolver = FakeResolver(cached=[])
            service = PrebuildService(resolver, workers=1)
            service.start(
                FakeForest([a, b]),
                cfg,
                roots={a},
            )
            service.wait()

            self.assertEqual(resolver.calls, [str(a)])
            self.assertEqual(service.status()["total"], 1)

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

    def test_stop_prevents_remaining_work(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d)
            paths = []
            for i in range(3):
                p = cfg / ("f%d.ltx" % i)
                p.write_text("[x]\n", encoding="utf-8")
                paths.append(p)

            resolver = BlockingResolver(cached=[])
            service = PrebuildService(resolver, workers=1)
            service.start(FakeForest(paths), cfg)
            self.assertTrue(
                resolver.entered.wait(2.0),
                "предсборка не начала работу",
            )

            service.stop()
            resolver.release.set()
            service.wait()

            status = service.status()
            self.assertEqual(status["state"], "idle")
            self.assertEqual(status["total"], 3)
            self.assertEqual(status["done"], 1)
            self.assertLess(len(resolver.calls), len(paths))
            self.assertEqual(len(resolver.calls), 1)

    def test_reports_failure(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d)
            path = cfg / "a.ltx"
            path.write_text("[x]\n", encoding="utf-8")

            resolver = FailingResolver(cached=[])
            service = PrebuildService(resolver, workers=1)
            with redirect_stderr(io.StringIO()):
                service.start(FakeForest([path]), cfg)
                service.wait()

            status = service.status()
            self.assertEqual(status["state"], "done")
            self.assertEqual(status["done"], 1)
            self.assertEqual(status["total"], 1)
            self.assertEqual(status["error"], "boom")


if __name__ == "__main__":
    unittest.main()
