# -*- coding: utf-8 -*-
"""Учёт виртуальных файлов, пришедших из TreeBuilder(overlay=...)."""

import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401
from tests.helpers import write_text

from app.workspace import WorkspaceService
from core.tools.cache import FileCacheSystem
from core.tree.builder import (
    TreeBuilder,
    add_virtual_file,
    remove_virtual_file,
)
from sdk.api import ProjectOverlay


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

            workspace = WorkspaceService()
            workspace.set_forest(forest)
            overlay = ProjectOverlay(
                source_files={new_path: new_text},
                new_files=frozenset({new_path}),
            )

            added, removed = workspace.apply_overlay(
                overlay,
                services=_ForestServices,
            )
            self.assertEqual((added, removed), (0, 0))
            self.assertIn(new_path, workspace.virtual_paths)

            # Проект без этого файла: VFS должен очиститься.
            added, removed = workspace.apply_overlay(
                ProjectOverlay(),
                services=_ForestServices,
            )
            self.assertEqual((added, removed), (0, 1))
            self.assertNotIn(new_path, forest.files)
            self.assertNotIn(new_path, workspace.virtual_paths)


if __name__ == "__main__":
    unittest.main()
