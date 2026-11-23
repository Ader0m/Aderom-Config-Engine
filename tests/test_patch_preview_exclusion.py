# -*- coding: utf-8 -*-
"""Исключение файлов и откат изменений в предпросмотре патча."""

import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PyQt5.QtCore import Qt
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
from ui.project_dialogs import ProjectPreviewDialog

_app = qapp()


def _preview(excluded=False):
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
        excluded=excluded,
        rollback_paths=(source,),
    )
    return ProjectPatchPreview(project=project, items=(item,))


class PatchPreviewExclusionTest(unittest.TestCase):
    def test_toggle_excluded_marks_only_preview_item(self):
        preview = _preview()
        dialog = ProjectPreviewDialog(preview)
        entry = dialog.list.item(0)

        dialog._toggle_excluded(entry)

        self.assertTrue(entry.data(Qt.UserRole).excluded)
        self.assertFalse(preview.items[0].excluded)
        self.assertIn("не включён", entry.text())
        self.assertIn("Исключено: 1", dialog._header.text())

        dialog._toggle_excluded(entry)

        self.assertFalse(entry.data(Qt.UserRole).excluded)
        self.assertNotIn("не включён", entry.text())
        self.assertNotIn("Исключено", dialog._header.text())

    def test_write_passes_excluded_flag_to_callback(self):
        preview = _preview()
        second = replace(
            preview.items[0],
            title="b.ltx",
            source_path=Path("gamedata") / "configs" / "b.ltx",
            rollback_paths=(Path("gamedata") / "configs" / "b.ltx",),
        )
        preview = replace(preview, items=(preview.items[0], second))
        captured = {}

        def on_write(updated):
            captured["preview"] = updated
            return False

        dialog = ProjectPreviewDialog(
            preview,
            on_write=on_write,
            write_enabled=True,
        )
        dialog._toggle_excluded(dialog.list.item(0))
        dialog._write()

        self.assertTrue(captured["preview"].items[0].excluded)
        self.assertEqual(len(captured["preview"].included_items), 1)
        # Исходный предпросмотр не мутируется.
        self.assertFalse(preview.items[0].excluded)

    def test_included_items_skips_excluded(self):
        preview = _preview(excluded=True)

        self.assertEqual(preview.included_items, ())
        self.assertEqual(len(preview.items), 1)

    def test_rollback_confirms_and_removes_item(self):
        preview = _preview()
        captured = {}

        def on_rollback(item):
            captured["item"] = item
            return True

        dialog = ProjectPreviewDialog(preview, on_rollback=on_rollback)

        with patch(
            "ui.project_dialogs.QMessageBox.question",
            return_value=QMessageBox.Yes,
        ) as question:
            dialog._rollback_entry(dialog.list.item(0))

        question.assert_called_once()
        self.assertIn("Вы уверены", question.call_args[0][2])
        self.assertIn("a.ltx", question.call_args[0][2])
        self.assertIs(captured["item"], preview.items[0])
        self.assertEqual(dialog.list.count(), 0)

    def test_rollback_cancelled_keeps_item(self):
        preview = _preview()
        called = []

        def on_rollback(item):
            called.append(item)
            return True

        dialog = ProjectPreviewDialog(preview, on_rollback=on_rollback)

        with patch(
            "ui.project_dialogs.QMessageBox.question",
            return_value=QMessageBox.No,
        ):
            dialog._rollback_entry(dialog.list.item(0))

        self.assertEqual(called, [])
        self.assertEqual(dialog.list.count(), 1)


if __name__ == "__main__":
    unittest.main()
