# -*- coding: utf-8 -*-
"""Проверки feature flags Этапа 0."""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from PyQt5.QtWidgets import QMessageBox

from tests.qt_support import qapp
from tests.support import SRC  # noqa: F401

from app.patching.models import (
    AssetChange,
    PreviewItem,
    ProjectPatchPreview,
)
from app.projects import Project
from core.patching.models import (
    GeneratedPatch,
    PatchFile,
    PatchPlan,
    PatchTarget,
    VerificationResult,
)
from ui.platform.feature_flags import (
    PATCH_WRITE_EXPERIMENTAL,
    patch_write_enabled,
)
from ui.platform.project_controller import ProjectControllerMixin
from ui.patch_preview import ProjectPreviewDialog

_app = qapp()


def _preview():
    project = Project(id="p1", name="Demo", root_dir=Path("."))
    source = Path("gamedata") / "configs" / "a.ltx"
    plan = PatchPlan(
        source_path=source,
        target=PatchTarget(
            source_path=source,
            root_path=Path("gamedata") / "configs" / "system.ltx",
        ),
        patch_mod="ACE Patch",
        patch_file_rel=Path("mod_ace.ltx"),
    )
    generated = GeneratedPatch(
        plan=plan,
        files=(
            PatchFile(
                relative_path=Path("gamedata/configs/mod_ace.ltx"),
                text="[a]\n",
            ),
        ),
        text="[a]\n",
        verification=VerificationResult(ok=True),
    )
    item = PreviewItem(
        title="a.ltx",
        source_path=source,
        plan=plan,
        generated=generated,
        kind="file",
        rollback_paths=(source,),
    )
    return ProjectPatchPreview(project=project, items=(item,))


class FeatureFlagTest(unittest.TestCase):
    def test_patch_write_flag_is_enabled_after_release_gate(self):
        self.assertTrue(PATCH_WRITE_EXPERIMENTAL)
        self.assertTrue(patch_write_enabled())

    def test_patch_write_flag_can_be_rolled_back(self):
        with patch(
            "ui.platform.feature_flags.PATCH_WRITE_EXPERIMENTAL",
            False,
        ):
            self.assertFalse(patch_write_enabled())

    def test_preview_dialog_default_is_fail_safe(self):
        calls = []
        dialog = ProjectPreviewDialog(
            _preview(),
            on_write=calls.append,
        )

        self.assertFalse(dialog._write_enabled)
        self.assertFalse(dialog.write_button.isEnabled())
        dialog._write()
        self.assertEqual(calls, [])

    def test_preview_dialog_blocks_write_when_flag_is_off(self):
        calls = []
        dialog = ProjectPreviewDialog(
            _preview(),
            on_write=calls.append,
            write_enabled=False,
        )

        self.assertFalse(dialog.write_button.isEnabled())
        dialog._write()
        self.assertEqual(calls, [])

    def test_preview_dialog_calls_write_when_enabled(self):
        calls = []
        dialog = ProjectPreviewDialog(
            _preview(),
            on_write=calls.append,
            write_enabled=True,
        )

        dialog._write()
        self.assertEqual(len(calls), 1)

    def test_preview_dialog_requests_cancel(self):
        cancelled = []
        dialog = ProjectPreviewDialog(
            _preview(),
            on_write=lambda _preview: True,
            on_cancel=lambda: cancelled.append(True),
            write_enabled=True,
        )

        dialog._write()
        self.assertTrue(dialog._writing)
        dialog._cancel_write()

        self.assertTrue(cancelled)
        self.assertFalse(dialog.cancel_button.isEnabled())

    def test_preview_dialog_blocks_unverified_write(self):
        preview = _preview()
        preview = replace(
            preview,
            items=(replace(preview.items[0], generated=None),),
        )
        calls = []
        dialog = ProjectPreviewDialog(
            preview,
            on_write=calls.append,
            write_enabled=True,
        )

        self.assertFalse(dialog.write_button.isEnabled())
        dialog._write()
        self.assertEqual(calls, [])

    def test_preview_dialog_blocks_failed_roundtrip(self):
        preview = _preview()
        preview = replace(
            preview,
            items=(
                replace(
                    preview.items[0],
                    roundtrip=VerificationResult(
                        ok=False,
                        issues=(),
                    ),
                ),
            ),
        )
        calls = []
        dialog = ProjectPreviewDialog(
            preview,
            on_write=calls.append,
            write_enabled=True,
        )

        self.assertFalse(dialog.write_button.isEnabled())
        dialog._write()
        self.assertEqual(calls, [])


class _Controller(ProjectControllerMixin):
    def __init__(self, services):
        self.services = services
        self.errors = []
        self.window = None
        self._forest = None
        self.project = None

    def show_error(self, message):
        self.errors.append(message)


def _risky_preview(project):
    source = Path("gamedata") / "configs" / "a.ltx"
    plan = PatchPlan(
        source_path=source,
        target=PatchTarget(
            source_path=source,
            root_path=Path("gamedata") / "configs" / "system.ltx",
        ),
        patch_mod="ACE Patch",
        patch_file_rel=Path("mod_ace.ltx"),
        changes=(
            AssetChange(
                kind="asset",
                path=source,
                description="danger",
                risk="confirm",
            ),
        ),
    )
    generated = GeneratedPatch(
        plan=plan,
        files=(
            PatchFile(
                relative_path=Path("gamedata/configs/mod_ace.ltx"),
                text="[a]\n",
            ),
        ),
        text="[a]\n",
        verification=VerificationResult(ok=True),
    )
    item = PreviewItem(
        title="a.ltx",
        source_path=source,
        plan=plan,
        generated=generated,
        kind="file",
    )
    return ProjectPatchPreview(project=project, items=(item,))


class ProjectControllerWriteGateTest(unittest.TestCase):
    def test_preview_dialog_receives_enabled_write_after_release_gate(self):
        controller = _Controller(Mock())
        controller.window = None

        with patch(
            "ui.platform.project_controller.patch_write_enabled",
            return_value=True,
        ), patch(
            "ui.platform.project_controller.ProjectPreviewDialog",
        ) as dialog_cls:
            controller._show_preview_dialog(_preview())

        self.assertTrue(dialog_cls.call_args.kwargs["write_enabled"])

    def test_write_preview_does_not_write_when_flag_is_off(self):
        services = Mock()
        controller = _Controller(services)

        with patch(
            "ui.platform.project_controller.patch_write_enabled",
            return_value=False,
        ):
            result = controller.write_preview(None)

        self.assertFalse(result)
        services.patch.patch_commit.commit.assert_not_called()
        self.assertTrue(controller.errors)
        self.assertIn("отключена", controller.errors[0])

    def test_risky_preview_requires_confirmation(self):
        project = Project(id="p1", name="Demo", root_dir=Path("."))
        services = Mock()
        services.patch.patch_commit.risk_keys.return_value = ("danger",)
        controller = _Controller(services)
        controller.project = Mock()
        controller.project.active = project
        preview = _risky_preview(project)

        with tempfile.TemporaryDirectory() as d:
            with patch(
                "ui.platform.project_controller.patch_write_enabled",
                return_value=True,
            ), patch(
                "ui.platform.project_controller.QFileDialog.getExistingDirectory",
                return_value=d,
            ), patch(
                "ui.platform.project_controller.QMessageBox.question",
                return_value=QMessageBox.No,
            ), patch(
                "ui.platform.project_controller.write_preview_use_case",
            ) as use_case:
                result = controller.write_preview(preview)

        self.assertFalse(result)
        use_case.assert_not_called()


if __name__ == "__main__":
    unittest.main()
