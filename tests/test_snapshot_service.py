# -*- coding: utf-8 -*-
"""Снимок VFS и привязка дерева к snapshot_id."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.support import SRC  # noqa: F401

from app.projects import ProjectService
from app.workspace import SnapshotService, WorkspaceSession
from core.cache.service import CacheService
from core.resolver.service import ResolverService
from core.services.tree import TreeServices
from core.tools.cache import FileCacheSystem, ResolvedCache
from core.tree.builder import TreeBuilder
from sdk.contracts import ProjectOverlay
from sdk.contracts.events import Events
from ui.platform_api import PlatformApi


class SnapshotServiceTest(unittest.TestCase):
    def test_same_files_and_overlay_return_same_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "configs"
            cfg.mkdir()
            (cfg / "system.ltx").write_text(
                "[system]\nx = 1\n",
                encoding="cp1251",
            )
            service = SnapshotService()

            first = service.ensure(cfg, cfg / "system.ltx", overlay_revision="r1")
            second = service.ensure(
                cfg,
                cfg / "system.ltx",
                overlay_revision="r1",
            )

            self.assertEqual(first.id, second.id)
            self.assertEqual(first.generation, second.generation)

    def test_external_file_change_needs_refresh(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "configs"
            cfg.mkdir()
            root = cfg / "system.ltx"
            root.write_text("[system]\nx = 1\n", encoding="cp1251")
            service = SnapshotService()

            first = service.ensure(cfg, root, overlay_revision="r1")
            root.write_text("[system]\nx = 222\n", encoding="cp1251")
            third = service.ensure(
                cfg,
                root,
                overlay_revision="r1",
                refresh=True,
            )

            self.assertNotEqual(first.id, third.id)
            self.assertGreater(third.generation, first.generation)

    def test_overlay_change_does_not_recompute_file_index(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "configs"
            cfg.mkdir()
            root = cfg / "system.ltx"
            root.write_text("[system]\nx = 1\n", encoding="cp1251")
            service = SnapshotService()

            first = service.ensure(cfg, root, overlay_revision="r1")
            second = service.update_overlay("r2")

            self.assertNotEqual(first.id, second.id)
            self.assertEqual(first.files_signature, second.files_signature)
            self.assertEqual(first.scan_dir, second.scan_dir)

    def test_set_forest_does_not_relabel_stale_forest(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "configs"
            cfg.mkdir()
            root = cfg / "system.ltx"
            root.write_text("[system]\nx = 1\n", encoding="cp1251")

            snapshot_service = SnapshotService()
            first = snapshot_service.ensure(
                cfg,
                root,
                overlay_revision="r1",
            )
            session = WorkspaceSession(snapshot_service=snapshot_service)
            forest = SimpleNamespace(
                scan_dir=str(cfg),
                system_root=str(root),
                cache_id=first.id,
                overlay_revision="r1",
            )

            latest = snapshot_service.update_overlay("r2")
            session.set_forest(forest)

            self.assertEqual(forest.cache_id, first.id)
            self.assertEqual(session.forest_snapshot_id, first.id)
            self.assertEqual(session.snapshot_id, latest.id)
            self.assertTrue(session.is_stale)

    def test_workspace_session_updates_overlay_generation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            session = WorkspaceSession()
            first = session.update_overlay("r1")
            second = session.update_overlay("r1")
            third = session.update_overlay("r2")

            self.assertTrue(first)
            self.assertFalse(second)
            self.assertTrue(third)
            self.assertGreater(session.generation, 0)


class TreeSnapshotTest(unittest.TestCase):
    def test_tree_cache_does_not_mix_snapshots(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "configs"
            cfg.mkdir()
            include_a = cfg / "a.ltx"
            include_b = cfg / "b.ltx"
            include_a.write_text("[a]\nx = 1\n", encoding="cp1251")
            include_b.write_text("[b]\ny = 1\n", encoding="cp1251")
            root = cfg / "system.ltx"
            root.write_text(
                '[system]\n#include "a.ltx"\n',
                encoding="cp1251",
            )

            cache = CacheService()
            builder = TreeBuilder(cache_service=cache)
            first = builder.build(
                cfg,
                root,
                use_cache=True,
                snapshot_id="snapshot-a",
            )
            self.assertEqual(first.cache_id, "snapshot-a")
            self.assertIn(include_a, first.include_children[root])

            root.write_text(
                '[system]\n#include "b.ltx"\n',
                encoding="cp1251",
            )
            second = builder.build(
                cfg,
                root,
                use_cache=True,
                snapshot_id="snapshot-b",
            )
            self.assertEqual(second.cache_id, "snapshot-b")
            self.assertIn(include_b, second.include_children[root])
            self.assertNotIn(include_a, second.include_children[root])

    def test_forest_cache_id_defaults_without_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "configs"
            cfg.mkdir()
            root = cfg / "system.ltx"
            root.write_text("[system]\nx = 1\n", encoding="cp1251")

            forest = TreeBuilder(cache_service=CacheService()).build(
                cfg,
                root,
                use_cache=True,
            )
            self.assertTrue(forest.cache_id)


class _Noop:
    def clear(self):
        pass

    def clear_memory(self):
        pass

    def invalidate_cache(self):
        pass


class CoreBuildSnapshotTest(unittest.TestCase):
    def test_clean_build_refreshes_snapshot_and_tree(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "configs"
            cfg.mkdir()
            include_a = cfg / "a.ltx"
            include_b = cfg / "b.ltx"
            include_a.write_text("[a]\nx = 1\n", encoding="cp1251")
            include_b.write_text("[b]\ny = 1\n", encoding="cp1251")
            root = cfg / "system.ltx"
            root.write_text(
                '[system]\n#include "a.ltx"\n',
                encoding="cp1251",
            )

            cache = FileCacheSystem()
            cache_service = CacheService()
            tree = TreeServices(
                TreeBuilder(cache=cache, cache_service=cache_service),
                None,
                snapshot_service=SnapshotService(),
                cache_service=cache_service,
                cache_control=SimpleNamespace(
                    invalidate_cache=lambda: None
                ),
            )

            first = tree.build_tree(cfg, root, clean=True)
            root.write_text(
                '[system]\n#include "b.ltx"\n',
                encoding="cp1251",
            )
            second = tree.build_tree(cfg, root, clean=True)

            self.assertNotEqual(first.cache_id, second.cache_id)
            self.assertIn(include_b, second.include_children[root])
            self.assertNotIn(include_a, second.include_children[root])


class ResolverSnapshotKeyTest(unittest.TestCase):
    def test_resolved_key_includes_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "configs"
            cfg.mkdir()
            root = cfg / "system.ltx"
            root.write_text("[system]\nx = 1\n", encoding="cp1251")

            service = ResolverService(
                FileCacheSystem(),
                ResolvedCache(),
            )
            first = service.resolved_key(
                root,
                SimpleNamespace(cache_id="snapshot-a"),
                cfg,
            )
            second = service.resolved_key(
                root,
                SimpleNamespace(cache_id="snapshot-b"),
                cfg,
            )
            self.assertNotEqual(first, second)
            self.assertEqual(first[2], "snapshot-a")
            self.assertEqual(second[2], "snapshot-b")


class TreeReadySnapshotTest(unittest.TestCase):
    def test_tree_ready_and_snapshot_event_carry_snapshot_id(self):
        class EventsFake:
            def __init__(self):
                self.published = []

            def subscribe(self, *_args, **_kwargs):
                return None

            def publish(self, name, payload=None):
                self.published.append((name, payload))

        class CommandsFake:
            def register(self, *_args, **_kwargs):
                return None

        events = EventsFake()
        services = SimpleNamespace(
            tree_summary=lambda _forest: None,
        )
        api = PlatformApi(
            events,
            CommandsFake(),
            None,
            services,
            None,
        )
        forest = SimpleNamespace(cache_id="snapshot-1")

        payload = api.publish_tree_ready(forest)

        self.assertEqual(payload.cache_id, "snapshot-1")
        names = [name for name, _payload in events.published]
        self.assertIn(Events.SNAPSHOT_CHANGED, names)
        self.assertIn(Events.TREE_READY, names)
        snapshot_payload = dict(events.published)[
            Events.SNAPSHOT_CHANGED
        ]
        self.assertEqual(snapshot_payload.snapshot_id, "snapshot-1")


    def test_tree_ready_keeps_forest_snapshot_when_overlay_changed(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "configs"
            cfg.mkdir()
            root = cfg / "system.ltx"
            root.write_text("[system]\nx = 1\n", encoding="cp1251")

            class EventsFake:
                def __init__(self):
                    self.published = []

                def subscribe(self, *_args, **_kwargs):
                    return None

                def publish(self, name, payload=None):
                    self.published.append((name, payload))

            class CommandsFake:
                def register(self, *_args, **_kwargs):
                    return None

            snapshot_service = SnapshotService()
            first = snapshot_service.ensure(
                cfg,
                root,
                overlay_revision="r1",
            )
            forest = SimpleNamespace(
                scan_dir=str(cfg),
                system_root=str(root),
                cache_id=first.id,
                overlay_revision="r1",
            )
            snapshot_service.update_overlay("r2")

            api = PlatformApi(
                EventsFake(),
                CommandsFake(),
                None,
                SimpleNamespace(tree_summary=lambda _forest: None),
                None,
                snapshot_service=snapshot_service,
            )
            payload = api.publish_tree_ready(forest)

            self.assertEqual(payload.cache_id, first.id)
            self.assertTrue(api.workspace.is_stale)
            self.assertNotEqual(
                api.workspace.snapshot_id,
                payload.cache_id,
            )


class OverlayRevisionTest(unittest.TestCase):
    def test_line_sources_change_revision_with_same_length(self):
        left = ProjectOverlay(
            resolved_edits={
                Path("system.ltx"): (
                    "old",
                    "new",
                    (SimpleNamespace(kind="file", display="a"),),
                ),
            }
        )
        right = ProjectOverlay(
            resolved_edits={
                Path("system.ltx"): (
                    "old",
                    "new",
                    (SimpleNamespace(kind="file", display="b"),),
                ),
            }
        )

        self.assertNotEqual(left.revision, right.revision)

    def test_project_overlay_revision_tracks_draft_revisions(self):
        with tempfile.TemporaryDirectory() as d:
            service = ProjectService(Path(d) / "projects")
            service.create("revision")
            path = Path(d) / "configs" / "system.ltx"
            service.update_draft(path, "first")
            first = service.project_overlay().revision
            service.update_draft(path, "second")
            second = service.project_overlay().revision

            self.assertTrue(first)
            self.assertNotEqual(first, second)

    def test_equal_overlays_have_equal_revision(self):
        values = {
            Path("system.ltx"): ("old", "new", ()),
        }
        self.assertEqual(
            ProjectOverlay(resolved_edits=values).revision,
            ProjectOverlay(resolved_edits=values).revision,
        )


if __name__ == "__main__":
    unittest.main()
