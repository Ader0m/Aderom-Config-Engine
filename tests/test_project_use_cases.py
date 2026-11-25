# -*- coding: utf-8 -*-
"""Use cases проекта: откат предпросмотра и запись патч-мода."""

import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from app.patching.commit import write_preview
from app.patching.models import (
    AssetChange,
    PreviewItem,
    ProjectPatchPreview,
)
from app.projects import (
    Project,
    ProjectError,
    ProjectService,
)
from app.projects.use_cases import rollback_preview_item
from core.api.patches import (
    GeneratedPatch,
    PatchCommitError,
    PatchFile,
    PatchPlan,
    PatchTarget,
    VerificationResult,
)


class _Forest:
    scan_dir = "C:/game/gamedata/configs"
    cache_id = "SNAP-1"


class _CommitResult:
    def __init__(self, paths):
        self.paths = tuple(paths)


class _Commit:
    def __init__(self, owner):
        self.owner = owner
        self.authorized = []
        self.commits = []
        self.audit_outcomes = []

    def risk_keys(self, plan):
        return tuple(
            str(change.description)
            for change in plan.changes
            if getattr(change, "risk", "") == "confirm"
        )

    def authorize(
        self,
        generated,
        confirmed_risks=(),
        roundtrip_report=None,
    ):
        risks = self.risk_keys(generated.plan)
        missing = [risk for risk in risks if risk not in confirmed_risks]
        if missing:
            raise PatchCommitError(
                "Требуется подтверждение: %s" % "; ".join(missing)
            )
        self.authorized.append(
            (generated, tuple(confirmed_risks), roundtrip_report)
        )
        if self.owner.on_authorize is not None:
            self.owner.on_authorize()
        return object()

    def commit(self, generated, authorization, mod_dir, **kwargs):
        self.commits.append((generated, authorization, mod_dir, kwargs))
        if self.owner.error is not None:
            raise self.owner.error
        if self.owner.fail_on_call == len(self.commits):
            raise RuntimeError("boom")
        file = generated.files[0]
        path = Path(mod_dir) / file.relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if file.data is not None:
            path.write_bytes(file.data)
        else:
            path.write_bytes(file.text.encode(file.encoding))
        if self.owner.on_commit is not None:
            self.owner.on_commit()
        return _CommitResult([path])

    def audit_outcome(self, result, status, reason=""):
        self.audit_outcomes.append((result, status, reason))


class _Services:
    def __init__(self, error=None):
        self.error = error
        self.fail_on_call = 0
        self.on_authorize = None
        self.on_commit = None
        self.patch_commit = _Commit(self)


def _preview_item(
    source: Path,
    *,
    risk="safe",
    excluded=False,
    ok=True,
    kind="file",
    relative_path=None,
    snapshot_id="SNAP-1",
) -> PreviewItem:
    relative_path = Path("a.ltx") if relative_path is None else relative_path
    plan = PatchPlan(
        source_path=source,
        target=PatchTarget(
            source_path=source,
            root_path=None,
            reason="test",
        ),
        patch_mod="ACE Patch - Proj",
        patch_file_rel=relative_path,
        changes=(
            AssetChange(
                kind="asset",
                path=source,
                description="test",
                risk=risk,
            ),
        ),
        snapshot_id=snapshot_id,
        format="ltx",
    )
    generated = GeneratedPatch(
        plan=plan,
        files=(
            PatchFile(relative_path=relative_path, text="[a]\n"),
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
        generated, authorization, mod_dir, kwargs = (
            services.patch_commit.commits[0]
        )
        self.assertIs(generated, preview.items[0].generated)
        self.assertIsNotNone(authorization)
        self.assertEqual(
            services.patch_commit.authorized[0][1],
            (),
        )
        self.assertEqual(mod_dir, result.mod_dir)
        self.assertFalse(kwargs["overwrite"])
        self.assertEqual(
            kwargs["forbidden_roots"],
            [Path(_Forest.scan_dir)],
        )
        self.assertEqual(len(kwargs["asset_rules"]), 1)

    def test_stale_snapshot_is_rejected_before_write(self):
        services = _Services()
        preview = self._preview(
            _preview_item(self.source, snapshot_id="SNAP-OLD"),
        )
        forest = _Forest()
        forest.cache_id = "SNAP-NEW"

        with self.assertRaises(ProjectError) as ctx:
            write_preview(
                self.service,
                services,
                preview,
                self.root,
                forest=forest,
                allow_write=True,
            )

        self.assertIn("Снимок", str(ctx.exception))
        self.assertEqual(services.patch_commit.commits, [])

    def test_snapshot_provider_checked_after_authorize(self):
        services = _Services()
        snapshot = {"id": "SNAP-1"}
        services.on_authorize = lambda: snapshot.__setitem__("id", "SNAP-2")
        preview = self._preview(_preview_item(self.source))

        with self.assertRaises(ProjectError) as ctx:
            write_preview(
                self.service,
                services,
                preview,
                self.root,
                forest=_Forest(),
                allow_write=True,
                snapshot_provider=lambda: snapshot["id"],
            )

        self.assertIn("Снимок VFS", str(ctx.exception))
        self.assertEqual(services.patch_commit.commits, [])

    def test_cancel_during_authorize_rejects_item(self):
        services = _Services()
        cancelled = {"value": False}
        services.on_authorize = lambda: cancelled.__setitem__("value", True)
        preview = self._preview(_preview_item(self.source))

        with self.assertRaises(ProjectError) as ctx:
            write_preview(
                self.service,
                services,
                preview,
                self.root,
                forest=_Forest(),
                allow_write=True,
                cancel_check=lambda: cancelled["value"],
            )

        self.assertIn("отменена", str(ctx.exception))
        self.assertEqual(services.patch_commit.commits, [])

    def test_cancel_during_commit_removes_current_file(self):
        services = _Services()
        cancelled = {"value": False}
        services.on_commit = lambda: cancelled.__setitem__("value", True)
        preview = self._preview(_preview_item(self.source))
        mod_path = (
            self.root
            / "ACE Patch - Proj"
            / "a.ltx"
        )

        with self.assertRaises(ProjectError) as ctx:
            write_preview(
                self.service,
                services,
                preview,
                self.root,
                forest=_Forest(),
                allow_write=True,
                cancel_check=lambda: cancelled["value"],
            )

        self.assertIn("отменена", str(ctx.exception))
        self.assertFalse(mod_path.exists())
        self.assertTrue(services.patch_commit.audit_outcomes)
        self.assertEqual(
            services.patch_commit.audit_outcomes[-1][1],
            "cancelled",
        )

    def test_second_item_failure_rolls_back_first(self):
        services = _Services()
        services.fail_on_call = 2
        preview = self._preview(
            _preview_item(self.source),
            _preview_item(
                self.root / "b.ltx",
                relative_path=Path("b.ltx"),
            ),
        )

        with self.assertRaises(ProjectError):
            write_preview(
                self.service,
                services,
                preview,
                self.root,
                forest=_Forest(),
                allow_write=True,
            )

        mod_dir = self.root / "ACE Patch - Proj"
        self.assertFalse((mod_dir / "a.ltx").exists())
        self.assertFalse((mod_dir / "b.ltx").exists())
        self.assertTrue(services.patch_commit.audit_outcomes)
        self.assertEqual(
            services.patch_commit.audit_outcomes[-1][1],
            "rolled_back",
        )

    def test_cancel_before_write_leaves_no_files(self):
        services = _Services()
        preview = self._preview(_preview_item(self.source))

        with self.assertRaises(ProjectError) as ctx:
            write_preview(
                self.service,
                services,
                preview,
                self.root,
                forest=_Forest(),
                allow_write=True,
                cancel_check=lambda: True,
            )

        self.assertIn("отменена", str(ctx.exception))
        self.assertEqual(services.patch_commit.commits, [])

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

    def test_risky_plan_requires_explicit_confirmation(self):
        services = _Services()
        preview = self._preview(
            _preview_item(self.source, risk="confirm"),
        )

        with self.assertRaises(ProjectError) as ctx:
            write_preview(
                self.service,
                services,
                preview,
                self.root,
                forest=_Forest(),
                allow_write=True,
            )
        self.assertIn("подтверждение", str(ctx.exception))
        self.assertEqual(services.patch_commit.commits, [])

        result = write_preview(
            self.service,
            services,
            preview,
            self.root,
            forest=_Forest(),
            allow_write=True,
            confirmed_risks=("test",),
        )

        self.assertEqual(result.skipped, ())
        self.assertEqual(
            services.patch_commit.authorized[0][1],
            ("test",),
        )
        self.assertEqual(len(services.patch_commit.commits), 1)

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
