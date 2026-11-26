# -*- coding: utf-8 -*-
"""Исключение файлов и откат изменений в предпросмотре патча."""

import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PyQt5.QtWidgets import QMessageBox

from tests.qt_support import qapp
from tests.support import SRC  # noqa: F401

from app.patching.models import PreviewItem, ProjectPatchPreview
from app.projects import Project
from core.patching.models import (
    GeneratedPatch,
    PatchFile,
    PatchPlan,
    PatchTarget,
    VerificationResult,
)
from ui.patch_preview import ProjectPreviewDialog

_app = qapp()


def _patch_path(item):
    relative = Path(item.generated.files[0].relative_path)
    parts = relative.parts
    if parts and parts[0].lower() == "gamedata":
        parts = parts[1:]
    return Path(*parts)


def _file_item(dialog, path):
    dialog.select_patch_path(path)
    return dialog.tree.currentItem()


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
        path = _patch_path(preview.items[0])
        entry = _file_item(dialog, path)

        dialog._toggle_excluded(entry)

        self.assertTrue(dialog._selector_items()[0].excluded)
        self.assertFalse(preview.items[0].excluded)
        self.assertIn("исключён", dialog.tree.currentItem().toolTip(0))
        self.assertIn("Исключено: 1", dialog._header.text())

        dialog._toggle_excluded(dialog.tree.currentItem())

        self.assertFalse(dialog._selector_items()[0].excluded)
        self.assertNotIn("исключён", dialog.tree.currentItem().toolTip(0))
        self.assertNotIn("Исключено", dialog._header.text())

    def test_write_passes_excluded_flag_to_callback(self):
        preview = _preview()
        second = replace(
            preview.items[0],
            title="b.ltx",
            source_path=Path("gamedata") / "configs" / "b.ltx",
            generated=replace(
                preview.items[0].generated,
                files=(
                    PatchFile(
                        relative_path=Path(
                            "gamedata/configs/mod_ace_b.ltx"
                        ),
                        text="[b]\n",
                    ),
                ),
            ),
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
        entry = _file_item(dialog, _patch_path(preview.items[0]))
        dialog._toggle_excluded(entry)
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
        entry = _file_item(dialog, _patch_path(preview.items[0]))

        with patch(
            "ui.patch_preview.QMessageBox.question",
            return_value=QMessageBox.Yes,
        ) as question:
            dialog._rollback_entry(entry)

        question.assert_called_once()
        self.assertIn("Вы уверены", question.call_args[0][2])
        self.assertIn("a.ltx", question.call_args[0][2])
        self.assertIs(captured["item"], preview.items[0])
        self.assertEqual(len(dialog.patch_paths()), 0)

    def test_rollback_cancelled_keeps_item(self):
        preview = _preview()
        called = []

        def on_rollback(item):
            called.append(item)
            return True

        dialog = ProjectPreviewDialog(preview, on_rollback=on_rollback)
        entry = _file_item(dialog, _patch_path(preview.items[0]))

        with patch(
            "ui.patch_preview.QMessageBox.question",
            return_value=QMessageBox.No,
        ):
            dialog._rollback_entry(entry)

        self.assertEqual(called, [])
        self.assertEqual(len(dialog.patch_paths()), 1)


if __name__ == "__main__":
    unittest.main()
