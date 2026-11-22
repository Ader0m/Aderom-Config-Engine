# -*- coding: utf-8 -*-
"""Проверки feature flags Этапа 0."""

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.qt_support import qapp
from tests.support import SRC  # noqa: F401

from app.projects import PreviewItem, Project, ProjectPatchPreview
from core.patching.models import PatchPlan, PatchTarget
from ui.platform.feature_flags import (
    PATCH_WRITE_EXPERIMENTAL,
    patch_write_enabled,
)
from ui.platform.project_controller import ProjectControllerMixin
from ui.project_dialogs import ProjectPreviewDialog

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
    item = PreviewItem(
        title="a.ltx",
        source_path=source,
        plan=plan,
        generated=None,
        kind="file",
        rollback_paths=(source,),
    )
    return ProjectPatchPreview(project=project, items=(item,))


class FeatureFlagTest(unittest.TestCase):
    def test_patch_write_flag_is_off_by_default(self):
        self.assertFalse(PATCH_WRITE_EXPERIMENTAL)
        self.assertFalse(patch_write_enabled())

    def test_patch_write_flag_can_be_enabled_explicitly(self):
        with patch(
            "ui.platform.feature_flags.PATCH_WRITE_EXPERIMENTAL",
            True,
        ):
            self.assertTrue(patch_write_enabled())

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


class _Controller(ProjectControllerMixin):
    def __init__(self, services):
        self.services = services
        self.errors = []

    def show_error(self, message):
        self.errors.append(message)


class ProjectControllerWriteGateTest(unittest.TestCase):
    def test_write_preview_does_not_write_when_flag_is_off(self):
        services = Mock()
        controller = _Controller(services)

        with patch(
            "ui.platform.project_controller.patch_write_enabled",
            return_value=False,
        ):
            result = controller.write_preview(None)

        self.assertFalse(result)
        services.write_patch.assert_not_called()
        self.assertTrue(controller.errors)
        self.assertIn("отключена", controller.errors[0])


if __name__ == "__main__":
    unittest.main()
