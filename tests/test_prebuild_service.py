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


class RevisionBlockingResolver(BlockingResolver):
    """Блокируется на первом проходе и различает ревизии overlay в ключе."""

    def __init__(self):
        super().__init__(cached=[])
        self.key_revisions = []
        self.value_revisions = []

    def resolved_key(self, path, forest, config_dir, overlay=None):
        revision = (overlay or {}).get("revision", "")
        self.key_revisions.append(revision)
        return "%s|%s" % (path, revision)

    def assemble_resolved(
        self,
        path,
        forest,
        config_dir,
        log=None,
        key=None,
        overlay=None,
    ):
        self.value_revisions.append((overlay or {}).get("revision", ""))
        return super().assemble_resolved(
            path,
            forest,
            config_dir,
            log=log,
            key=key,
            overlay=overlay,
        )


class RevisionResolver(FakeResolver):
    """Быстрый резолвер, различающий ревизии overlay в ключе."""

    def resolved_key(self, path, forest, config_dir, overlay=None):
        revision = (overlay or {}).get("revision", "")
        return "%s|%s" % (path, revision)


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

    def test_new_start_does_not_interrupt_running_pass(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d)
            path = cfg / "a.ltx"
            path.write_text("[a]\n", encoding="utf-8")

            resolver = RevisionBlockingResolver()
            service = PrebuildService(resolver, workers=1)
            forest = FakeForest([path])

            service.start(forest, cfg, overlay={"revision": "1"})
            self.assertTrue(
                resolver.entered.wait(2.0),
                "предсборка не начала работу",
            )

            # Новая ревизия проекта приходит во время сборки: текущий
            # проход не должен быть убит.
            service.start(forest, cfg, overlay={"revision": "2"})
            status = service.status()
            self.assertEqual(status["generation"], 2)
            self.assertEqual(status["done_generation"], 0)
            self.assertTrue(status["pending"])
            self.assertEqual(len(resolver.calls), 1)

            resolver.release.set()
            service.wait()

            status = service.status()
            self.assertEqual(status["state"], "done")
            self.assertEqual(status["done_generation"], 2)
            self.assertFalse(status["pending"])
            self.assertEqual(len(resolver.calls), 2)
            self.assertEqual(resolver.value_revisions, ["1", "2"])

    def test_wait_covers_pending_generation(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d)
            path = cfg / "a.ltx"
            path.write_text("[a]\n", encoding="utf-8")

            resolver = RevisionBlockingResolver()
            service = PrebuildService(resolver, workers=1)
            forest = FakeForest([path])

            service.start(forest, cfg, overlay={"revision": "1"})
            self.assertTrue(resolver.entered.wait(2.0))
            service.start(forest, cfg, overlay={"revision": "2"})

            finished = threading.Event()

            def waiter():
                service.wait()
                finished.set()

            thread = threading.Thread(target=waiter, daemon=True)
            thread.start()
            self.assertFalse(
                finished.wait(0.3),
                "wait вернулся до отложенной сборки",
            )

            resolver.release.set()
            self.assertTrue(finished.wait(3.0))
            thread.join(1.0)
            self.assertEqual(service.status()["done_generation"], 2)

    def test_burst_of_starts_is_not_lost(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d)
            path = cfg / "a.ltx"
            path.write_text("[a]\n", encoding="utf-8")

            resolver = RevisionResolver(cached=[])
            service = PrebuildService(resolver, workers=1)
            forest = FakeForest([path])

            for index in range(200):
                service.start(forest, cfg, overlay={"revision": str(index)})

            service.wait(timeout=5)

            status = service.status()
            self.assertFalse(status["pending"])
            self.assertEqual(status["state"], "done")
            self.assertEqual(status["generation"], 200)
            self.assertEqual(status["done_generation"], 200)

    def test_stop_cancels_pending_generation(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d)
            path = cfg / "a.ltx"
            path.write_text("[a]\n", encoding="utf-8")

            resolver = RevisionBlockingResolver()
            service = PrebuildService(resolver, workers=1)
            forest = FakeForest([path])

            service.start(forest, cfg, overlay={"revision": "1"})
            self.assertTrue(resolver.entered.wait(2.0))
            service.start(forest, cfg, overlay={"revision": "2"})

            service.stop()
            resolver.release.set()
            service.wait()

            status = service.status()
            self.assertEqual(status["state"], "idle")
            self.assertFalse(status["pending"])
            self.assertEqual(status["done_generation"], 0)
            self.assertEqual(len(resolver.calls), 1)
            self.assertEqual(resolver.value_revisions, ["1"])

    def test_start_after_stop_runs_again(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d)
            path = cfg / "a.ltx"
            path.write_text("[a]\n", encoding="utf-8")

            resolver = RevisionResolver(cached=[])
            service = PrebuildService(resolver, workers=1)
            forest = FakeForest([path])

            service.start(forest, cfg, overlay={"revision": "1"})
            service.wait()
            service.stop()

            service.start(forest, cfg, overlay={"revision": "2"})
            service.wait()

            status = service.status()
            self.assertEqual(status["state"], "done")
            self.assertEqual(status["done_generation"], 2)
            self.assertEqual(len(resolver.calls), 2)

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
