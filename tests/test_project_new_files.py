# -*- coding: utf-8 -*-
"""Новые файлы проекта и кэш решений патчера."""

import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.patching.models import (
    GeneratedPatch,
    PatchPlan,
    PatchResult,
    PatchTarget,
    VerificationCheck,
    VerificationResult,
)
from app.projects import ProjectService


class _Forest:
    def __init__(self, scan_dir):
        self.scan_dir = str(scan_dir)
        self.files = []
        self.xml_files = []
        self.rel_paths = {}
        self.real_paths = {}
        self.include_parents = {}
        self.display_children = {}
        self.roots = []
        self.system_root = ""


class _CountingPatchPlanner:
    def __init__(self, owner):
        self.owner = owner

    def plan_document_change(
        self,
        source_path,
        before_text,
        after_text,
        forest=None,
        options=None,
    ):
        return self.owner._plan(source_path)


class _CountingServices:
    """Считает, сколько раз планировщик реально запускался."""

    def __init__(self):
        self.calls = 0
        self.patch = _CountingPatchPlanner(self)

    def _plan(self, source_path):
        self.calls += 1
        return PatchPlan(
            source_path=Path(source_path),
            target=PatchTarget(
                source_path=Path(source_path),
                root_path=Path(source_path),
            ),
            patch_mod="Test",
            patch_file_rel=Path("a.ltx"),
            changes=(object(),),
        )

    def plan_ltx_patch(self, *args, **kwargs):
        plan = self._plan(args[0])
        return PatchResult(
            plan=plan,
            generated=self.generate_patch(plan),
        )

    def plan_ltx_override(self, *args, **kwargs):
        plan = self._plan(args[0])
        generated = GeneratedPatch(
            plan=plan,
            files=(),
            text="generated",
            verification=VerificationResult(
                ok=True,
                checks=(VerificationCheck("ok", True),),
            ),
        )
        return PatchResult(
            plan=plan,
            generated=generated,
            notes=(),
        )

    def generate_patch(self, plan):
        return GeneratedPatch(
            plan=plan,
            files=(),
            text="generated",
            verification=VerificationResult(
                ok=True,
                checks=(VerificationCheck("ok", True),),
            ),
        )


class NewFilePreviewTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = Path(self._tmp.name) / "gamedata" / "configs"
        self.cfg.mkdir(parents=True)
        self.forest = _Forest(self.cfg)
        self.service = ProjectService(Path(self._tmp.name) / "projects")
        self.service.create("Проект")

    def test_new_ltx_is_copied_by_exact_path(self):
        path = (
            self.cfg
            / "items"
            / "mod_item_base_~ace_проект.ltx"
        )
        text = "[new_item]:base_item\nkind =\n"
        self.service.save_draft(path, text)

        preview = self.service.build_preview(object(), self.forest)

        self.assertEqual(len(preview.items), 1)
        item = preview.items[0]
        self.assertEqual(item.kind, "new_file")
        self.assertTrue(item.ok, item.error)
        self.assertEqual(
            item.generated.files[0].relative_path,
            Path("gamedata")
            / "configs"
            / "items"
            / "mod_item_base_~ace_проект.ltx",
        )
        self.assertEqual(item.generated.files[0].text, text)
        self.assertEqual(item.generated.text, text)

    def test_new_xml_is_copied_by_exact_path(self):
        path = self.cfg / "text" / "rus" / "st_ace_проект.xml"
        text = "<string_table></string_table>\n"
        self.service.save_draft(path, text)

        preview = self.service.build_preview(object(), self.forest)

        item = preview.items[0]
        self.assertEqual(item.kind, "new_file")
        self.assertTrue(item.ok, item.error)
        self.assertEqual(
            item.generated.files[0].relative_path,
            Path("gamedata")
            / "configs"
            / "text"
            / "rus"
            / "st_ace_проект.xml",
        )

    def test_all_modes_keep_new_files_separate(self):
        first = self.cfg / "items" / "mod_item_base_~ace_p.ltx"
        second = self.cfg / "text" / "rus" / "st_ace_p.xml"
        self.service.save_draft(first, "[one]\n")
        self.service.save_draft(second, "<string_table></string_table>\n")

        for mode in ("root", "on_file", "override"):
            self.service.set_patch_mode(mode)
            preview = self.service.build_preview(object(), self.forest)

            self.assertEqual(len(preview.items), 2, mode)
            self.assertTrue(
                all(
                    item.kind == "new_file"
                    for item in preview.items
                ),
                mode,
            )
            self.assertFalse(
                any(item.kind == "root" for item in preview.items),
                mode,
            )


class NewFileDraftModelTest(unittest.TestCase):
    def test_new_flag_and_revision_are_persisted(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "projects"
            cfg = Path(d) / "gamedata" / "configs"
            cfg.mkdir(parents=True)
            path = cfg / "items" / "mod_item_base_~ace_p.ltx"

            service = ProjectService(base)
            project = service.create("Proj")
            draft = service.save_draft(path, "[one]\n")

            self.assertTrue(draft.is_new)
            self.assertEqual(draft.revision, 0)

            service.update_draft(path, "[one]\nkind = a\n")
            self.assertEqual(draft.revision, 1)
            service.update_draft(path, "[one]\nkind = a\n")
            self.assertEqual(draft.revision, 1)

            service.save_draft(path)
            project_id = project.id
            service.close()
            service.load(project_id)

            loaded = service.get_draft(path)
            self.assertTrue(loaded.is_new)
            self.assertEqual(loaded.revision, 1)

    def test_existing_file_draft_is_not_new(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "a.ltx"
            path.write_text("[a]\n", encoding="windows-1251")

            service = ProjectService(Path(d) / "projects")
            service.create("Proj")
            draft = service.update_draft(path, "[a]\nx = 1\n")

            self.assertFalse(draft.is_new)
            self.assertEqual(draft.revision, 0)

    def test_missing_required_draft_fields_is_invalid(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "projects"
            service = ProjectService(base)
            project = service.create("Proj")
            path = Path(d) / "a.ltx"
            path.write_text("[a]\n", encoding="windows-1251")
            service.save_draft(path, "[a]\nx = 1\n")

            draft_file = next(
                (project.root_dir / "drafts").glob("*.json")
            )
            import json

            data = json.loads(draft_file.read_text(encoding="utf-8"))
            data.pop("is_new")
            draft_file.write_text(
                json.dumps(data, ensure_ascii=False),
                encoding="utf-8",
            )

            project_id = project.id
            service.close()
            loaded = service.load(project_id)
            self.assertEqual(loaded.drafts, {})


class PreviewCacheTest(unittest.TestCase):
    def test_planner_result_is_reused_until_revision_changes(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "gamedata" / "configs"
            cfg.mkdir(parents=True)
            source = cfg / "a.ltx"
            source.write_text("[a]\nx = 1\n", encoding="windows-1251")
            forest = _Forest(cfg)
            forest.files = [source]
            forest.roots = [source]

            service = ProjectService(Path(d) / "projects")
            service.create("Proj", save_mode="on_file")
            service.update_draft(source, "[a]\nx = 2\n")
            planner = _CountingServices()

            first = service.build_preview(planner, forest)
            second = service.build_preview(planner, forest)

            self.assertEqual(planner.calls, 1)
            self.assertEqual(len(first.items), 1)
            self.assertEqual(
                first.items[0].generated.text,
                second.items[0].generated.text,
            )

            service.update_draft(source, "[a]\nx = 3\n")
            service.build_preview(planner, forest)
            self.assertEqual(planner.calls, 2)

    def test_mode_change_invalidates_cache(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "gamedata" / "configs"
            cfg.mkdir(parents=True)
            source = cfg / "a.ltx"
            source.write_text("[a]\nx = 1\n", encoding="windows-1251")
            forest = _Forest(cfg)
            forest.files = [source]
            forest.roots = [source]

            service = ProjectService(Path(d) / "projects")
            service.create("Proj", save_mode="on_file")
            service.update_draft(source, "[a]\nx = 2\n")
            planner = _CountingServices()

            service.build_preview(planner, forest)
            service.set_patch_mode("override")
            service.build_preview(planner, forest)

            self.assertEqual(planner.calls, 2)


if __name__ == "__main__":
    unittest.main()
