# -*- coding: utf-8 -*-
"""Асинхронная запись патч-мода через TaskService."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PyQt5.QtWidgets import QMessageBox

from tests.qt_support import qapp
from tests.support import SRC  # noqa: F401

from app.projects import PreviewItem, Project, ProjectPatchPreview
from core.patching.models import (
    GeneratedPatch,
    PatchFile,
    PatchPlan,
    PatchTarget,
    VerificationResult,
)
from ui.platform.project_controller import ProjectControllerMixin

_app = qapp()


def _preview(project):
    source = Path("gamedata") / "configs" / "a.ltx"
    plan = PatchPlan(
        source_path=source,
        target=PatchTarget(
            source_path=source,
            root_path=Path("gamedata") / "configs" / "system.ltx",
        ),
        patch_mod="ACE Patch",
        patch_file_rel=Path("mod_ace.ltx"),
        snapshot_id="SNAP",
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


class _Tasks:
    def __init__(self):
        self.calls = []
        self.cancelled = []

    def run(self, fn, on_done=None, on_log=None, owner=None, **kwargs):
        self.calls.append(
            {
                "fn": fn,
                "on_done": on_done,
                "on_log": on_log,
                "owner": owner,
            }
        )
        return object()

    def cancel_owner(self, owner):
        self.cancelled.append(owner)
        return 1


class _AssetTypes:
    def patch_rules(self):
        return ()


class _Controller(ProjectControllerMixin):
    def __init__(self, tasks, services, project, forest):
        self.tasks = tasks
        self.services = services
        self.window = None
        self.project = SimpleNamespace(active=project)
        self.workspace = SimpleNamespace(snapshot_id="SNAP")
        self._forest = forest
        self.asset_types = _AssetTypes()
        self._patch_write_running = False
        self.errors = []
        self.statuses = []
        self.logs = []

    def show_error(self, message):
        self.errors.append(message)

    def set_status(self, message, timeout=0):
        self.statuses.append(message)

    def log(self, message):
        self.logs.append(message)


class PatchWriteAsyncTest(unittest.TestCase):
    def _controller(self, d):
        project = Project(id="p1", name="Demo", root_dir=Path(d))
        forest = SimpleNamespace(
            scan_dir=str(Path(d) / "gamedata" / "configs"),
            cache_id="SNAP",
        )
        services = Mock()
        services.patch_commit.risk_keys.return_value = ()
        tasks = _Tasks()
        return (
            project,
            forest,
            services,
            _Controller(tasks, services, project, forest),
        )

    def test_write_starts_task_and_finishes_on_ui_callback(self):
        with tempfile.TemporaryDirectory() as d:
            project, forest, _services, controller = self._controller(d)
            preview = _preview(project)
            result = SimpleNamespace(
                mod_dir=Path(d) / "ACE Patch - Demo",
                written=(Path(d) / "ACE Patch - Demo" / "a.ltx",),
                skipped=(),
                excluded_count=0,
                audit=(),
            )

            with patch(
                "ui.platform.project_controller.patch_write_enabled",
                return_value=True,
            ), patch(
                "ui.platform.project_controller.QFileDialog.getExistingDirectory",
                return_value=d,
            ), patch(
                "ui.platform.project_controller.write_preview_use_case",
                return_value=result,
            ) as use_case, patch(
                "ui.platform.project_controller.QMessageBox.information",
            ):
                started = controller.write_preview(preview)

                self.assertTrue(started)
                self.assertTrue(controller._patch_write_running)
                self.assertEqual(use_case.call_count, 0)
                self.assertEqual(len(controller.tasks.calls), 1)
                call = controller.tasks.calls[0]
                self.assertEqual(call["owner"], "p1")

                background = call["fn"](lambda _message: None)
                use_case.assert_called_once()
                call["on_done"](background, None)

            self.assertFalse(controller._patch_write_running)
            self.assertTrue(controller.statuses)
            self.assertIn("Патч-мод создан", controller.statuses[-1])

    def test_second_write_is_rejected_while_running(self):
        with tempfile.TemporaryDirectory() as d:
            project, _forest, _services, controller = self._controller(d)
            controller._patch_write_running = True

            with patch(
                "ui.platform.project_controller.patch_write_enabled",
                return_value=True,
            ):
                started = controller.write_preview(_preview(project))

            self.assertFalse(started)
            self.assertTrue(controller.errors)
            self.assertIn("уже выполняется", controller.errors[0])

    def test_cancel_action_requests_owner_cancellation(self):
        with tempfile.TemporaryDirectory() as d:
            _project, _forest, _services, controller = self._controller(d)
            controller._patch_write_running = True

            started = controller.cancel_patch_write()

            self.assertTrue(started)
            self.assertEqual(controller.tasks.cancelled, ["p1"])
            self.assertTrue(controller.statuses)
            self.assertIn("Отмена", controller.statuses[-1])

    def test_roundtrip_reports_are_attached_to_preview(self):
        with tempfile.TemporaryDirectory() as d:
            project, forest, services, controller = self._controller(d)
            preview = _preview(project)
            report = VerificationResult(ok=True)
            controller._show_preview_dialog = Mock()

            controller._on_roundtrip_done(
                preview,
                [(preview.items[0], report)],
                None,
                project,
                forest,
            )

            shown = controller._show_preview_dialog.call_args[0][0]
            self.assertIs(shown.items[0].roundtrip, report)

    def test_task_error_resets_running_state(self):
        with tempfile.TemporaryDirectory() as d:
            project, _forest, _services, controller = self._controller(d)
            controller._patch_write_running = True

            controller._on_patch_write_done(None, RuntimeError("disk"))

            self.assertFalse(controller._patch_write_running)
            self.assertTrue(controller.errors)
            self.assertIn("disk", controller.errors[0])


if __name__ == "__main__":
    unittest.main()
