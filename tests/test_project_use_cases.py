# -*- coding: utf-8 -*-
"""Use cases проекта: откат предпросмотра и запись патч-мода."""

import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from app.patching.commit import write_preview
from app.projects import (
    AssetChange,
    PreviewItem,
    Project,
    ProjectError,
    ProjectPatchPreview,
    ProjectService,
)
from app.projects.use_cases import rollback_preview_item
from core.api.patches import (
    GeneratedPatch,
    PatchFile,
    PatchPlan,
    PatchTarget,
    VerificationResult,
)


class _Forest:
    scan_dir = "C:/game/gamedata/configs"


class _Services:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def write_patch(self, generated, mod_dir, **kwargs):
        self.calls.append((generated, mod_dir, kwargs))
        if self.error is not None:
            raise self.error
        return [Path(mod_dir) / generated.files[0].relative_path]


def _preview_item(
    source: Path,
    *,
    risk="safe",
    excluded=False,
    ok=True,
    kind="file",
) -> PreviewItem:
    plan = PatchPlan(
        source_path=source,
        target=PatchTarget(
            source_path=source,
            root_path=None,
            reason="test",
        ),
        patch_mod="ACE Patch - Proj",
        patch_file_rel=Path("a.ltx"),
        changes=(
            AssetChange(
                kind="asset",
                path=source,
                description="test",
                risk=risk,
            ),
        ),
        format="ltx",
    )
    generated = GeneratedPatch(
        plan=plan,
        files=(
            PatchFile(relative_path=Path("a.ltx"), text="[a]\n"),
        ),
        text="[a]\n",
        verification=VerificationResult(ok=ok, checks=()),
    )
    return PreviewItem(
        title=source.name,
        source_path=source,
        plan=plan,
        generated=generated,
        excluded=excluded,
        kind=kind,
    )


class RollbackUseCaseTest(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.service = ProjectService(self.root / "projects")

    def test_removes_text_draft(self):
        self.service.create("Proj")
        source = self.root / "configs" / "a.ltx"
        source.parent.mkdir(parents=True)
        source.write_text("[a]\nx = 1\n", encoding="cp1251")
        self.service.update_draft(source, "[a]\nx = 2\n")
        item = PreviewItem(
            title="a.ltx",
            source_path=source,
            plan=None,
            generated=None,
            rollback_paths=(source,),
        )

        result = rollback_preview_item(self.service, item)

        self.assertEqual(result.removed_paths, (source,))
        self.assertEqual(result.removed_resolved, ())
        self.assertIn(source, result.invalidate_paths)
        self.assertIsNone(self.service.get_draft(source))

    def test_removes_resolved_draft_and_invalidates_root(self):
        self.service.create("Proj")
        root = self.root / "gamedata" / "configs"
        root.mkdir(parents=True)
        self.service.update_resolved_draft(
            "tab",
            root,
            "ROOT",
            "[a]\nx = 1\n",
            "[a]\nx = 2\n",
        )
        item = PreviewItem(
            title="ROOT",
            source_path=root,
            plan=None,
            generated=None,
            rollback_resolved_ids=("tab",),
        )

        result = rollback_preview_item(self.service, item)

        self.assertEqual(result.removed_paths, ())
        self.assertEqual(result.removed_resolved, ("tab",))
        self.assertIn(root, result.invalidate_paths)
        self.assertIsNone(self.service.get_resolved_draft("tab"))

    def test_requires_active_project(self):
        item = PreviewItem(
            title="a.ltx",
            source_path=self.root / "a.ltx",
            plan=None,
            generated=None,
        )
        with self.assertRaises(ProjectError) as ctx:
            rollback_preview_item(self.service, item)
        self.assertIn("не активен", str(ctx.exception))

    def test_missing_draft_is_reported(self):
        self.service.create("Proj")
        item = PreviewItem(
            title="a.ltx",
            source_path=self.root / "a.ltx",
            plan=None,
            generated=None,
            rollback_paths=(self.root / "a.ltx",),
        )
        with self.assertRaises(ProjectError) as ctx:
            rollback_preview_item(self.service, item)
        self.assertIn("черновик уже отсутствует", str(ctx.exception))


class WritePreviewUseCaseTest(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.service = ProjectService(self.root / "projects")
        self.project = self.service.create("Proj")
        self.source = self.root / "configs" / "a.ltx"
        self.source.parent.mkdir(parents=True)
        self.source.write_text("[a]\n", encoding="cp1251")

    def _preview(self, *items, project=None):
        return ProjectPatchPreview(
            project=self.project if project is None else project,
            items=tuple(items),
        )

    def test_requires_allow_write(self):
        preview = self._preview(_preview_item(self.source))
        with self.assertRaises(ProjectError) as ctx:
            write_preview(
                self.service,
                _Services(),
                preview,
                self.root,
                forest=_Forest(),
            )
        self.assertIn("отключена", str(ctx.exception))

    def test_requires_built_tree(self):
        preview = self._preview(_preview_item(self.source))
        with self.assertRaises(ProjectError) as ctx:
            write_preview(
                self.service,
                _Services(),
                preview,
                self.root,
                forest=None,
                allow_write=True,
            )
        self.assertIn("дерева", str(ctx.exception))

    def test_writes_included_items(self):
        services = _Services()
        preview = self._preview(_preview_item(self.source))

        result = write_preview(
            self.service,
            services,
            preview,
            self.root,
            forest=_Forest(),
            allow_write=True,
            asset_rules=(object(),),
        )

        self.assertEqual(result.mod_dir, self.root / "ACE Patch - Proj")
        self.assertEqual(
            result.written,
            (result.mod_dir / "a.ltx",),
        )
        self.assertEqual(result.skipped, ())
        self.assertEqual(result.excluded_count, 0)
        _generated, mod_dir, kwargs = services.calls[0]
        self.assertEqual(mod_dir, result.mod_dir)
        self.assertFalse(kwargs["overwrite"])
        self.assertEqual(
            kwargs["forbidden_roots"],
            [Path(_Forest.scan_dir)],
        )
        self.assertEqual(len(kwargs["asset_rules"]), 1)

    def test_rejects_stale_project(self):
        other = Project(
            id="other",
            name="Other",
            root_dir=self.root / "other",
        )
        preview = self._preview(
            _preview_item(self.source),
            project=other,
        )
        with self.assertRaises(ProjectError) as ctx:
            write_preview(
                self.service,
                _Services(),
                preview,
                self.root,
                forest=_Forest(),
                allow_write=True,
            )
        self.assertIn("изменился", str(ctx.exception))

    def test_rejects_all_excluded(self):
        preview = self._preview(
            _preview_item(self.source, excluded=True),
        )
        with self.assertRaises(ProjectError) as ctx:
            write_preview(
                self.service,
                _Services(),
                preview,
                self.root,
                forest=_Forest(),
                allow_write=True,
            )
        self.assertIn("исключены", str(ctx.exception))

    def test_confirms_risky_plan(self):
        services = _Services()
        preview = self._preview(_preview_item(self.source, risk="confirm"))

        write_preview(
            self.service,
            services,
            preview,
            self.root,
            forest=_Forest(),
            allow_write=True,
        )

        generated = services.calls[0][0]
        self.assertTrue(generated.plan.confirmed)

    def test_wraps_write_error(self):
        services = _Services(error=RuntimeError("disk"))
        preview = self._preview(_preview_item(self.source))
        with self.assertRaises(ProjectError) as ctx:
            write_preview(
                self.service,
                services,
                preview,
                self.root,
                forest=_Forest(),
                allow_write=True,
            )
        message = str(ctx.exception)
        self.assertIn("a.ltx", message)
        self.assertIn("disk", message)

    def test_failed_items_are_skipped(self):
        services = _Services()
        preview = self._preview(
            _preview_item(self.source),
            _preview_item(
                self.root / "b.ltx",
                ok=False,
            ),
            _preview_item(
                self.root / "c.ltx",
                excluded=True,
            ),
        )

        result = write_preview(
            self.service,
            services,
            preview,
            self.root,
            forest=_Forest(),
            allow_write=True,
        )

        self.assertEqual(result.skipped, ("b.ltx",))
        self.assertEqual(result.excluded_count, 1)

    def test_all_failed_is_not_written(self):
        preview = self._preview(
            _preview_item(self.source, ok=False),
        )
        with self.assertRaises(ProjectError) as ctx:
            write_preview(
                self.service,
                _Services(),
                preview,
                self.root,
                forest=_Forest(),
                allow_write=True,
            )
        self.assertIn("не записаны", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
