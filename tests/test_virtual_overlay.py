# -*- coding: utf-8 -*-
"""Черновики проекта как виртуальные файлы VFS."""

import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401
from tests.helpers import write_text

from sdk.api import ProjectOverlay

from core.resolver.prebuild import PrebuildService
from core.resolver.service import ResolverService
from core.tools.cache import FileCacheSystem, ResolvedCache
from core.tree.builder import (
    TreeBuilder,
    add_virtual_file,
    remove_virtual_file,
)


class VirtualOverlayTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = Path(self._tmp.name) / "gamedata" / "configs"

        self.system = write_text(
            self.cfg / "system.ltx",
            '[system]\n#include "items/item_base.ltx"\n',
        )
        self.item_base = write_text(
            self.cfg / "items" / "item_base.ltx",
            "[base_item]\nkind = i_food\ncost = 10\n",
        )
        self.mod_path = (
            self.cfg
            / "items"
            / "mod_item_base_~ace_project.ltx"
        )
        self.mod_text = (
            "[new_item]:base_item\n"
            "kind =\n"
            "cost =\n"
            "inv_name = st_ace_project_name\n"
        )

    def test_new_file_is_added_to_tree_and_attached_to_dltx_root(self):
        forest = TreeBuilder(FileCacheSystem()).build(
            self.cfg,
            self.system,
            use_cache=False,
            overlay={self.mod_path: self.mod_text},
        )

        self.assertIn(self.mod_path, forest.files)
        self.assertIn(
            self.mod_path,
            forest.display_children[self.item_base],
        )
        self.assertIn(
            self.mod_path,
            forest.dltx_attachments.get(self.item_base, set()),
        )

    def test_virtual_mod_is_not_loaded_twice_for_its_root(self):
        overlay = {self.mod_path: self.mod_text}
        forest = TreeBuilder(FileCacheSystem()).build(
            self.cfg,
            self.system,
            use_cache=False,
            overlay=overlay,
        )
        service = ResolverService(
            cache=FileCacheSystem(),
            resolved_cache=ResolvedCache(),
        )
        logs = []

        text, _sources = service.assemble_resolved(
            self.item_base,
            forest,
            self.cfg,
            overlay=overlay,
            log=logs.append,
        )

        self.assertEqual(text.count("[new_item]"), 1)
        self.assertTrue(
            any(
                "Пропущено" in message
                and self.mod_path.name in message
                for message in logs
            ),
            logs,
        )

    def test_extra_overlay_load_is_logged(self):
        overlay = {self.mod_path: self.mod_text}
        forest = TreeBuilder(FileCacheSystem()).build(
            self.cfg,
            self.system,
            use_cache=False,
            overlay=overlay,
        )
        service = ResolverService(
            cache=FileCacheSystem(),
            resolved_cache=ResolvedCache(),
        )
        logs = []

        service.assemble_resolved(
            self.system,
            forest,
            self.cfg,
            overlay=overlay,
            log=logs.append,
        )

        self.assertTrue(
            any(
                "Выполнено" in message
                and self.mod_path.name in message
                for message in logs
            ),
            logs,
        )

    def test_new_file_can_be_added_to_existing_tree(self):
        forest = TreeBuilder(FileCacheSystem()).build(
            self.cfg,
            self.system,
            use_cache=False,
        )
        self.assertNotIn(self.mod_path, forest.files)

        self.assertTrue(
            add_virtual_file(forest, self.mod_path, self.mod_text)
        )

        self.assertIn(self.mod_path, forest.files)
        self.assertIn(
            self.mod_path,
            forest.display_children[self.item_base],
        )
        self.assertEqual(
            forest.total_files,
            3,
        )

        self.assertTrue(remove_virtual_file(forest, self.mod_path))
        self.assertNotIn(self.mod_path, forest.files)
        self.assertNotIn(
            self.mod_path,
            forest.display_children.get(self.item_base, ()),
        )
        self.assertEqual(forest.total_files, 2)

    def test_resolver_reads_virtual_file(self):
        overlay = {self.mod_path: self.mod_text}
        forest = TreeBuilder(FileCacheSystem()).build(
            self.cfg,
            self.system,
            use_cache=False,
            overlay=overlay,
        )
        service = ResolverService(
            cache=FileCacheSystem(),
            resolved_cache=ResolvedCache(),
        )

        text, _sources = service.assemble_resolved(
            self.system,
            forest,
            self.cfg,
            overlay=overlay,
        )

        self.assertIn("[new_item]", text)
        self.assertIn("inv_name = st_ace_project_name", text)

    def test_tree_cache_key_includes_overlay_content(self):
        builder = TreeBuilder(FileCacheSystem())
        first = builder.build(
            self.cfg,
            self.system,
            use_cache=True,
            overlay={self.mod_path: self.mod_text},
        )
        same = builder.build(
            self.cfg,
            self.system,
            use_cache=True,
            overlay={self.mod_path: self.mod_text},
        )
        changed = builder.build(
            self.cfg,
            self.system,
            use_cache=True,
            overlay={
                self.mod_path: self.mod_text.replace(
                    "kind =",
                    "kind = i_med",
                )
            },
        )

        self.assertIs(first, same)
        self.assertIsNot(first, changed)
        self.assertIn(self.mod_path, changed.files)

    def test_resolved_override_of_leaf_updates_parent_root(self):
        forest = TreeBuilder(FileCacheSystem()).build(
            self.cfg,
            self.system,
            use_cache=False,
        )
        service = ResolverService(
            cache=FileCacheSystem(),
            resolved_cache=ResolvedCache(),
        )
        leaf_text, sources = service.assemble_resolved(
            self.item_base,
            forest,
            self.cfg,
        )
        edited = leaf_text.replace("kind = i_food", "kind = i_med")
        self.assertNotEqual(leaf_text, edited)

        overlay = ProjectOverlay(
            resolved_edits={
                self.item_base: (leaf_text, edited, sources),
            },
        )
        text, _ = service.assemble_resolved(
            self.system,
            forest,
            self.cfg,
            overlay=overlay,
        )
        self.assertIn("kind = i_med", text)

        edited_again = leaf_text.replace(
            "kind = i_food",
            "kind = i_heal",
        )
        overlay_again = ProjectOverlay(
            resolved_edits={
                self.item_base: (leaf_text, edited_again, sources),
            },
        )
        text_again, _ = service.assemble_resolved(
            self.system,
            forest,
            self.cfg,
            overlay=overlay_again,
        )
        self.assertIn("kind = i_heal", text_again)
        self.assertNotIn("kind = i_med", text_again)

    def test_prebuild_stores_resolved_edit_under_matching_key(self):
        forest = TreeBuilder(FileCacheSystem()).build(
            self.cfg,
            self.system,
            use_cache=False,
        )
        service = ResolverService(
            cache=FileCacheSystem(),
            resolved_cache=ResolvedCache(),
        )
        leaf_text, sources = service.assemble_resolved(
            self.item_base,
            forest,
            self.cfg,
        )
        edited = leaf_text.replace("kind = i_food", "kind = i_med")
        overlay = ProjectOverlay(
            resolved_edits={
                self.item_base: (leaf_text, edited, sources),
            },
        )

        prebuild = PrebuildService(service, workers=1)
        prebuild.start(forest, self.cfg, overlay=overlay)
        prebuild.wait()

        text, _ = service.assemble_resolved(
            self.system,
            forest,
            self.cfg,
            overlay=overlay,
        )
        self.assertIn("kind = i_med", text)

    def test_overlay_content_changes_resolved_cache_key(self):
        first_overlay = {self.mod_path: self.mod_text}
        forest = TreeBuilder(FileCacheSystem()).build(
            self.cfg,
            self.system,
            use_cache=False,
            overlay=first_overlay,
        )
        service = ResolverService(
            cache=FileCacheSystem(),
            resolved_cache=ResolvedCache(),
        )
        first, _ = service.assemble_resolved(
            self.system,
            forest,
            self.cfg,
            overlay=first_overlay,
        )

        second_overlay = {
            self.mod_path: self.mod_text.replace(
                "kind =",
                "kind = i_med",
            )
        }
        second, _ = service.assemble_resolved(
            self.system,
            forest,
            self.cfg,
            overlay=second_overlay,
        )

        self.assertNotEqual(first, second)
        self.assertIn("kind = i_med", second)


if __name__ == "__main__":
    unittest.main()
