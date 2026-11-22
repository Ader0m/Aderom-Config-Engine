# -*- coding: utf-8 -*-
"""Учёт виртуальных файлов, пришедших из TreeBuilder(overlay=...)."""

import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401
from tests.helpers import write_text

from app.workspace import SnapshotService, WorkspaceSession
from core.tools.cache import FileCacheSystem
from core.tree.builder import (
    TreeBuilder,
    add_virtual_file,
    remove_virtual_file,
)
from sdk.contracts import ProjectOverlay


class _ForestServices:
    add_virtual_file = staticmethod(add_virtual_file)
    remove_virtual_file = staticmethod(remove_virtual_file)


class WorkspaceTrackingTest(unittest.TestCase):
    def test_files_from_build_overlay_are_removed_on_next_apply(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "gamedata" / "configs"
            system = write_text(cfg / "system.ltx", "[system]\n")
            new_path = cfg / "items" / "new_item.ltx"
            new_text = "[new_item]\ncost = 1\n"

            forest = TreeBuilder(FileCacheSystem()).build(
                cfg,
                system,
                use_cache=False,
                overlay={new_path: new_text},
            )
            self.assertIn(new_path, forest.files)

            workspace = WorkspaceSession()
            workspace.set_forest(forest)
            overlay = ProjectOverlay(
                source_files={new_path: new_text},
                new_files=frozenset({new_path}),
            )
            workspace.update_overlay(overlay.revision)
            forest.overlay_revision = overlay.revision
            forest.cache_id = workspace.snapshot_id

            added, removed = workspace.apply_overlay(
                overlay,
                services=_ForestServices,
            )
            self.assertEqual((added, removed), (0, 0))
            self.assertIn(new_path, workspace.virtual_paths)

            # Проект без этого файла: VFS должен очиститься.
            empty_overlay = ProjectOverlay()
            workspace.update_overlay(empty_overlay.revision)
            forest.overlay_revision = empty_overlay.revision
            forest.cache_id = workspace.snapshot_id
            added, removed = workspace.apply_overlay(
                empty_overlay,
                services=_ForestServices,
            )
            self.assertEqual((added, removed), (0, 1))
            self.assertNotIn(new_path, forest.files)
            self.assertNotIn(new_path, workspace.virtual_paths)

    def test_apply_overlay_does_not_clear_stale_after_snapshot_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "gamedata" / "configs"
            system = write_text(cfg / "system.ltx", "[system]\n")
            new_path = cfg / "items" / "new_item.ltx"
            new_text = "[new_item]\ncost = 1\n"

            forest = TreeBuilder(FileCacheSystem()).build(
                cfg,
                system,
                use_cache=False,
            )
            forest.cache_id = "forest-old"
            forest.overlay_revision = "r1"

            snapshot_service = SnapshotService()
            snapshot_service.ensure(
                cfg,
                system,
                overlay_revision="r1",
            )
            workspace = WorkspaceSession(
                snapshot_service=snapshot_service,
            )
            workspace.set_forest(forest)
            self.assertTrue(workspace.is_stale)

            overlay = ProjectOverlay(
                source_files={new_path: new_text},
                new_files=frozenset({new_path}),
                revision_token="r1",
            )
            added, removed = workspace.apply_overlay(
                overlay,
                services=_ForestServices,
            )

            self.assertEqual((added, removed), (0, 0))
            self.assertNotIn(new_path, forest.files)
            self.assertEqual(forest.cache_id, "forest-old")
            self.assertEqual(forest.overlay_revision, "r1")
            self.assertTrue(workspace.is_stale)

    def test_apply_overlay_blocks_forest_without_overlay_revision(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "gamedata" / "configs"
            system = write_text(
                cfg / "system.ltx",
                '[system]\n#include "a.ltx"\n',
            )
            write_text(cfg / "a.ltx", "[a]\nx = 1\n")
            write_text(cfg / "b.ltx", "[b]\ny = 1\n")
            new_path = cfg / "items" / "new_item.ltx"

            forest = TreeBuilder(FileCacheSystem()).build(
                cfg,
                system,
                use_cache=False,
            )
            forest.cache_id = "built-without-overlay"

            workspace = WorkspaceSession()
            workspace.set_forest(forest)
            overlay = ProjectOverlay(
                source_files={
                    system: '[system]\n#include "b.ltx"\n',
                    new_path: "[new_item]\ncost = 1\n",
                },
                new_files=frozenset({new_path}),
                revision_token="r1",
            )

            added, removed = workspace.apply_overlay(
                overlay,
                services=_ForestServices,
            )

            self.assertEqual((added, removed), (0, 0))
            self.assertNotIn(new_path, forest.files)
            self.assertEqual(
                forest.cache_id,
                "built-without-overlay",
            )
            self.assertTrue(workspace.is_stale)

    def test_apply_overlay_does_not_relabel_forest_from_other_revision(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "gamedata" / "configs"
            system = write_text(cfg / "system.ltx", "[system]\n")
            new_path = cfg / "items" / "new_item.ltx"
            new_text = "[new_item]\ncost = 1\n"

            forest = TreeBuilder(FileCacheSystem()).build(
                cfg,
                system,
                use_cache=False,
            )
            forest.cache_id = "built-r1"
            forest.overlay_revision = "r1"

            workspace = WorkspaceSession()
            workspace.set_forest(forest)
            overlay = ProjectOverlay(
                source_files={new_path: new_text},
                new_files=frozenset({new_path}),
                revision_token="r2",
            )

            added, removed = workspace.apply_overlay(
                overlay,
                services=_ForestServices,
            )

            self.assertEqual((added, removed), (0, 0))
            self.assertNotIn(new_path, forest.files)
            self.assertNotIn(new_path, workspace.virtual_paths)
            self.assertEqual(forest.cache_id, "built-r1")
            self.assertEqual(forest.overlay_revision, "r1")
            self.assertTrue(workspace.is_stale)

    def test_apply_overlay_without_file_changes_keeps_tree_revision(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "gamedata" / "configs"
            system = write_text(cfg / "system.ltx", "[system]\n")
            forest = TreeBuilder(FileCacheSystem()).build(
                cfg,
                system,
                use_cache=False,
            )
            forest.cache_id = "built-r1"
            forest.overlay_revision = "r1"

            workspace = WorkspaceSession()
            workspace.set_forest(forest)
            overlay = ProjectOverlay(revision_token="r2")

            added, removed = workspace.apply_overlay(
                overlay,
                services=_ForestServices,
            )

            self.assertEqual((added, removed), (0, 0))
            self.assertEqual(forest.cache_id, "built-r1")
            self.assertEqual(forest.overlay_revision, "r1")
            self.assertTrue(workspace.is_stale)


if __name__ == "__main__":
    unittest.main()
