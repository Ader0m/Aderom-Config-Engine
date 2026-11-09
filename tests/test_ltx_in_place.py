# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.models import LineSource
from core.patching.ltx.in_place import LtxInPlaceService
from core.patching.ltx.override import LtxOverrideService
from core.patching.models import PatchOptions
from core.patching.planner import PatchPlanner
from ui.project import ProjectService


class InPlaceServices:
    def __init__(self):
        self.patch = PatchPlanner()
        self.ltx_in_place = LtxInPlaceService()

    def plan_ltx_in_place(self, *args, **kwargs):
        return self.ltx_in_place.plan(*args, **kwargs)


class Forest:
    def __init__(self, config_dir, files, parents=None):
        self.scan_dir = str(config_dir)
        self.files = list(files)
        self.xml_files = []
        self.include_parents = dict(parents or {})
        self.rel_paths = {
            path: str(path.relative_to(config_dir)).replace("/", "\\")
            for path in files
        }


class LtxInPlaceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name) / "configs"
        (self.config / "items").mkdir(parents=True)
        self.source = self.config / "items" / "items.ltx"
        self.source.write_bytes(b"[item]\nx = old\n")

    def test_raw_edit_creates_patch_next_to_opened_file(self):
        forest = Forest(self.config, [self.source])
        result = LtxInPlaceService().plan(
            self.source,
            "[item]\nx = old\n",
            "[item]\nx = new\n",
            forest=forest,
            options=PatchOptions(project_tag="proj"),
        )
        self.assertTrue(result.generated.verification.ok)
        self.assertEqual(result.plan.format, "ltx_in_place")
        file = result.generated.files[0]
        self.assertEqual(
            file.relative_path,
            Path("gamedata") / "configs" / "items"
            / "mod_items_~ace_proj.ltx",
        )
        self.assertIn("![item]", file.text)
        self.assertIn("x = new", file.text)

    def test_resolved_without_line_sources_uses_opened_file(self):
        forest = Forest(self.config, [self.source])
        result = LtxInPlaceService().plan(
            self.source,
            "[item]\nx = old\n",
            "[item]\nx = new\n",
            line_sources=(),
            forest=forest,
            options=PatchOptions(project_tag="proj"),
            resolved=True,
        )
        self.assertTrue(result.generated.verification.ok)
        self.assertEqual(
            result.generated.files[0].relative_path.name,
            "mod_items_~ace_proj.ltx",
        )
        self.assertIn("x = new", result.generated.files[0].text)

    def test_resolved_line_with_owner_patches_owner_file(self):
        root = self.config / "system.ltx"
        root.write_bytes(b'[system]\n#include "items/items.ltx"\n')
        child = self.source
        forest = Forest(self.config, [root, child], {child: {root}})
        result = LtxInPlaceService().plan(
            root,
            "[item]\nx = old\n",
            "[item]\nx = new\n",
            line_sources=(
                LineSource(kind="original", path=root, line=1),
                LineSource(kind="file", path=child, line=2),
            ),
            forest=forest,
            options=PatchOptions(project_tag="proj"),
            resolved=True,
        )
        self.assertTrue(result.generated.verification.ok)
        file = result.generated.files[0]
        self.assertEqual(
            file.relative_path,
            Path("gamedata") / "configs" / "items"
            / "mod_items_~ace_proj.ltx",
        )
        self.assertIn("x = new", file.text)

    def test_original_line_patches_opened_file_not_root(self):
        root = self.config / "system.ltx"
        root.write_bytes(b"[item]\nx = old\n")
        child = self.source
        child.write_bytes(b"[item]\nx = old\n")
        forest = Forest(self.config, [root, child], {child: {root}})
        result = LtxInPlaceService().plan(
            child,
            "[item]\nx = old\n",
            "[item]\nx = new\n",
            line_sources=(
                LineSource(kind="original", path=child, line=1),
                LineSource(kind="original", path=child, line=2),
            ),
            forest=forest,
            options=PatchOptions(project_tag="proj"),
            resolved=True,
        )
        file = result.generated.files[0]
        self.assertEqual(file.relative_path.name, "mod_items_~ace_proj.ltx")
        self.assertEqual(
            file.relative_path.parent,
            Path("gamedata") / "configs" / "items",
        )

    def test_patch_owner_creates_patch_for_root(self):
        root = self.config / "system.ltx"
        root.write_bytes(b"[item]\nx = old\n")
        patch = self.config / "mod_system_old.ltx"
        patch.write_bytes(b"![item]\nx = old\n")
        forest = Forest(self.config, [root, patch])
        result = LtxInPlaceService().plan(
            root,
            "[item]\nx = old\n",
            "[item]\nx = new\n",
            line_sources=(
                LineSource(kind="original", path=root, line=1),
                LineSource(kind="patch", path=patch, line=2),
            ),
            forest=forest,
            options=PatchOptions(project_tag="proj"),
            resolved=True,
        )
        file = result.generated.files[0]
        self.assertEqual(file.relative_path.name, "mod_system_~ace_proj.ltx")
        self.assertIn("x = new", file.text)

    def test_project_explicit_in_place_mode(self):
        projects = ProjectService(Path(self.temp.name) / "projects")
        projects.create("inplace", save_mode="in_place")
        projects.update_draft(self.source, "[item]\nx = new\n")
        forest = Forest(self.config, [self.source])
        preview = projects.build_preview(InPlaceServices(), forest)
        self.assertEqual(len(preview.items), 1)
        item = preview.items[0]
        self.assertTrue(item.ok, item.error)
        self.assertEqual(item.plan.format, "ltx_in_place")
        self.assertEqual(
            item.generated.files[0].relative_path.name,
            "mod_items_~ace_inplace.ltx",
        )

    def test_resolved_line_sources_persist(self):
        projects = ProjectService(Path(self.temp.name) / "projects")
        project = projects.create("sources", save_mode="in_place")
        projects.update_resolved_draft(
            "resolved:items",
            self.source,
            "items.ltx",
            "[item]\nx = old\n",
            "[item]\nx = new\n",
            line_sources=(
                LineSource(kind="original", path=self.source, line=1),
                LineSource(
                    kind="file",
                    display="winner.ltx",
                    path=self.source,
                    line=2,
                ),
            ),
        )
        projects.save_resolved_draft("resolved:items")
        project_id = project.id
        projects.close()
        projects.load(project_id)

        draft = projects.get_resolved_draft("resolved:items")
        self.assertEqual(len(draft.line_sources), 2)
        self.assertEqual(draft.line_sources[0].kind, "original")
        self.assertEqual(draft.line_sources[1].display, "winner.ltx")
        self.assertEqual(draft.line_sources[1].line, 2)


class LtxOverrideWinnerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name) / "configs"
        (self.config / "items").mkdir(parents=True)

    def test_original_line_copies_opened_file(self):
        root = self.config / "system.ltx"
        child = self.config / "items" / "items.ltx"
        root.write_bytes(b"[item]\nx = old\n")
        child.write_bytes(b"[item]\nx = old\n")
        forest = Forest(self.config, [root, child], {child: {root}})

        result = LtxOverrideService().plan(
            child,
            "[item]\nx = old\n",
            "[item]\nx = new\n",
            line_sources=(
                LineSource(kind="original", path=child, line=1),
                LineSource(kind="original", path=child, line=2),
            ),
            forest=forest,
            options=PatchOptions(),
            resolved=True,
        )
        self.assertTrue(result.generated.verification.ok)
        self.assertEqual(
            result.generated.files[0].relative_path,
            Path("gamedata") / "configs" / "items" / "items.ltx",
        )
        self.assertIn("x = new", result.generated.files[0].text)


if __name__ == "__main__":
    unittest.main()
