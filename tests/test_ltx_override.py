# -*- coding: utf-8 -*-
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.models import LineSource
from core.patching.ltx.override import LtxOverrideError, LtxOverrideService
from core.patching.models import PatchOptions
from core.patching.planner import PatchPlanner
from core.patching.writer import PatchWriter
from ui.project import ProjectService


class OverrideServices:
    def __init__(self):
        self.patch = PatchPlanner()
        self.ltx_override = LtxOverrideService()

    def plan_ltx_override(self, *args, **kwargs):
        return self.ltx_override.plan(*args, **kwargs)


class Forest:
    def __init__(self, config_dir, files):
        self.scan_dir = str(config_dir)
        self.files = list(files)
        self.xml_files = []
        self.include_parents = {}
        self.rel_paths = {
            path: str(path.relative_to(config_dir)).replace("/", "\\")
            for path in files
        }


class LtxOverrideTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name) / "configs"
        self.config.mkdir()
        self.source = self.config / "items" / "items.ltx"
        self.source.parent.mkdir()
        self.source.write_bytes(b"[item]\nx = old\n")
        self.forest = Forest(self.config, [self.source])
        self.service = LtxOverrideService()

    def test_raw_ltx_full_copy(self):
        result = self.service.plan(
            self.source,
            "[item]\nx = old\n",
            "[item]\nx = new\n",
            forest=self.forest,
            options=PatchOptions(),
        )
        self.assertTrue(result.generated.verification.ok)
        self.assertEqual(len(result.generated.files), 1)
        self.assertEqual(
            result.generated.files[0].relative_path,
            Path("gamedata") / "configs" / "items" / "items.ltx",
        )
        self.assertIn("x = new", result.generated.files[0].text)
        writer = PatchWriter(
            require_verification=True,
            require_resolver=True,
        )
        paths = writer.write(
            replace(
                result.generated,
                plan=replace(result.generated.plan, confirmed=True),
            ),
            Path(self.temp.name) / "mod",
        )
        self.assertTrue(paths[0].is_file())

    def test_resolved_only_original_lines(self):
        baseline = "[item]\nx = old\n"
        edited = "[item]\nx = new\n"
        result = self.service.plan(
            self.source,
            baseline,
            edited,
            line_sources=(
                LineSource(kind="original", path=self.source, line=1),
                LineSource(kind="original", path=self.source, line=2),
            ),
            forest=self.forest,
            options=PatchOptions(),
            resolved=True,
        )
        self.assertTrue(result.generated.verification.ok)
        self.assertIn("x = new", result.generated.files[0].text)

    def test_project_override_mode(self):
        projects = ProjectService(Path(self.temp.name) / "projects")
        projects.create("override", save_mode="override")
        projects.update_draft(self.source, "[item]\nx = new\n")
        preview = projects.build_preview(OverrideServices(), self.forest)
        self.assertEqual(len(preview.items), 1)
        item = preview.items[0]
        self.assertTrue(item.ok, item.error)
        self.assertEqual(item.plan.format, "ltx_override")
        self.assertIn("x = new", item.generated.files[0].text)

    def test_project_override_resolved_without_sources(self):
        projects = ProjectService(Path(self.temp.name) / "projects")
        projects.create("override", save_mode="override")
        projects.update_resolved_draft(
            "resolved:items",
            self.source,
            "items.ltx (resolved)",
            "[item]\nx = old\n",
            "[item]\nx = new\n",
            line_sources=(),
        )
        preview = projects.build_preview(OverrideServices(), self.forest)
        self.assertEqual(len(preview.items), 1)
        item = preview.items[0]
        self.assertTrue(item.ok, item.error)
        self.assertEqual(item.plan.format, "ltx_override")
        self.assertIn("x = new", item.generated.files[0].text)

    def test_resolved_without_line_sources_uses_opened_file(self):
        result = self.service.plan(
            self.source,
            "[item]\nx = old\n",
            "[item]\nx = new\n",
            line_sources=(),
            forest=self.forest,
            options=PatchOptions(),
            resolved=True,
        )
        self.assertTrue(result.generated.verification.ok)
        self.assertEqual(
            result.generated.files[0].relative_path,
            Path("gamedata") / "configs" / "items" / "items.ltx",
        )
        self.assertIn("x = new", result.generated.files[0].text)

    def test_resolved_file_winner_is_copied(self):
        child = self.config / "items" / "child.ltx"
        child.write_bytes(b"[item]\nx = old\n")
        forest = Forest(self.config, [self.source, child])
        result = self.service.plan(
            self.source,
            "[item]\nx = old\n",
            "[item]\nx = new\n",
            line_sources=(
                LineSource(kind="original", path=self.source, line=1),
                LineSource(
                    kind="file",
                    display="child.ltx",
                    path=child,
                    line=0,
                ),
            ),
            forest=forest,
            options=PatchOptions(),
            resolved=True,
        )
        self.assertTrue(result.generated.verification.ok)
        self.assertEqual(
            result.generated.files[0].relative_path.name,
            "child.ltx",
        )
        self.assertIn("x = new", result.generated.files[0].text)

    def test_winner_display_path_without_physical_path(self):
        mod_file = (
            Path(self.temp.name)
            / "MO2"
            / "mods"
            / "GRA Bolt"
            / "gamedata"
            / "configs"
            / "items"
            / "trade"
            / "presets"
            / "trade_presets.ltx"
        )
        mod_file.parent.mkdir(parents=True)
        mod_file.write_bytes(b"[ammo]\nammo_vog_25 = 1\n")

        result = self.service.plan(
            self.source,
            "[ammo]\nammo_vog_25 = 1\n",
            "[ammo]\nammo_vog_25 = 2\n",
            line_sources=(
                LineSource(kind="original", path=self.source, line=1),
                LineSource(
                    kind="file",
                    display=(
                        "MO2\\GRA Bolt\\gamedata\\configs\\items\\"
                        "trade\\presets\\trade_presets.ltx"
                    ),
                    path=None,
                    line=0,
                ),
            ),
            forest=self.forest,
            options=PatchOptions(),
            resolved=True,
        )
        self.assertTrue(result.generated.verification.ok)
        self.assertEqual(
            result.generated.files[0].relative_path,
            Path("gamedata") / "configs" / "items" / "trade"
            / "presets" / "trade_presets.ltx",
        )
        self.assertIn("ammo_vog_25 = 2", result.generated.files[0].text)

    def test_resolved_patch_winner_is_overridden(self):
        patch = self.config / "items" / "mod_items_~other.ltx"
        patch.write_bytes(b"[item]\nx = old\n")
        forest = Forest(self.config, [self.source, patch])
        result = self.service.plan(
            self.source,
            "[item]\nx = old\n",
            "[item]\nx = new\n",
            line_sources=(
                LineSource(kind="original", path=self.source, line=1),
                LineSource(
                    kind="patch",
                    display="mod_items_~other.ltx",
                    path=patch,
                    line=0,
                ),
            ),
            forest=forest,
            options=PatchOptions(),
            resolved=True,
        )
        self.assertTrue(result.generated.verification.ok)
        self.assertEqual(
            result.generated.files[0].relative_path.name,
            "mod_items_~other.ltx",
        )
        self.assertIn("x = new", result.generated.files[0].text)
        self.assertTrue(
            any(
                issue.code == "ltx_override_dltx_patch_winner"
                for issue in result.plan.warnings
            )
        )


if __name__ == "__main__":
    unittest.main()
