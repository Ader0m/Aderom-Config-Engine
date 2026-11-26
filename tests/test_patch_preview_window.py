# -*- coding: utf-8 -*-
"""Окно предпросмотра патча: VFS-дерево, QScintilla и ленивое обновление."""

import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.qt_support import qapp
from tests.support import SRC  # noqa: F401

from app.patching.models import PreviewItem, ProjectPatchPreview
from app.projects import (
    Project,
    ProjectDraft,
    ProjectResolvedDraft,
)
from core.models import LineSource
from core.patching.models import (
    GeneratedPatch,
    PatchFile,
    PatchPlan,
    PatchTarget,
    VerificationResult,
)
from ui.editor import _CHANGE_MARKER
from ui.patch_preview import ProjectPreviewDialog
from ui.platform.project_controller import ProjectControllerMixin
from ui.platform_api import PlatformApi

_app = qapp()


def _preview(paths):
    project = Project(id="p1", name="Demo", root_dir=Path("."))
    items = []
    for relative, text in paths:
        source = Path("gamedata") / relative
        plan = PatchPlan(
            source_path=source,
            target=PatchTarget(
                source_path=source,
                root_path=Path("gamedata/configs/system.ltx"),
            ),
            patch_mod="ACE Patch",
            patch_file_rel=relative,
        )
        generated = GeneratedPatch(
            plan=plan,
            files=(
                PatchFile(
                    relative_path=Path("gamedata") / relative,
                    text=text,
                ),
            ),
            text=text,
            verification=VerificationResult(ok=True),
        )
        items.append(
            PreviewItem(
                title=Path(relative).name,
                source_path=source,
                plan=plan,
                generated=generated,
                kind="file",
                rollback_paths=(source,),
            )
        )
    return ProjectPatchPreview(project=project, items=tuple(items))


class PatchPreviewTreeTest(unittest.TestCase):
    def test_tree_contains_only_affected_vfs_paths(self):
        preview = _preview(
            (
                (
                    Path("configs/wpo/weapons/importer.ltx"),
                    "[importer]\nkind = i_food\n",
                ),
                (
                    Path("configs/items/medkit.ltx"),
                    "[medkit]\nkind = i_med\n",
                ),
            )
        )
        dialog = ProjectPreviewDialog(preview)
        try:
            file_item = dialog.tree.item_for_path(
                Path("configs/wpo/weapons/importer.ltx")
            )
            self.assertIsNotNone(file_item)
            self.assertEqual(file_item.text(0), "importer.ltx")

            weapons = file_item.parent()
            wpo = weapons.parent()
            configs = wpo.parent()
            self.assertEqual(weapons.text(0), "weapons")
            self.assertEqual(wpo.text(0), "wpo")
            self.assertEqual(configs.text(0), "configs")

            self.assertEqual(
                dialog.patch_paths(),
                (
                    Path("configs/wpo/weapons/importer.ltx"),
                    Path("configs/items/medkit.ltx"),
                ),
            )
        finally:
            dialog.force_close()

    def test_selected_file_is_shown_read_only(self):
        text = "[importer]\nkind = i_food\ncost = 10\n"
        preview = _preview(
            ((Path("configs/wpo/weapons/importer.ltx"), text),)
        )
        dialog = ProjectPreviewDialog(
            preview,
            log_lines=("Патч: подготовка",),
        )
        try:
            path = Path("configs/wpo/weapons/importer.ltx")
            self.assertTrue(dialog.select_patch_path(path))

            self.assertEqual(dialog.code.text(), text)
            self.assertTrue(dialog.code.editor.isReadOnly())
            self.assertEqual(
                Path(dialog.code.current_file),
                path,
            )

            log_text = dialog.log_view.toPlainText()
            self.assertIn("Патч: подготовка", log_text)
            self.assertIn("Лог файла: gamedata", log_text)
            self.assertIn("Статус: OK", log_text)
        finally:
            dialog.force_close()

    def test_preview_shows_draft_change_markers(self):
        relative = Path("configs/creatures/monster.ltx")
        current = "[monster]\nkind = i_food\n"
        source = Path("gamedata") / relative
        plan = PatchPlan(
            source_path=source,
            target=PatchTarget(
                source_path=source,
                root_path=Path("gamedata/configs/system.ltx"),
            ),
            patch_mod="ACE Patch",
            patch_file_rel=relative,
        )
        generated = GeneratedPatch(
            plan=plan,
            files=(
                PatchFile(
                    relative_path=Path("gamedata") / relative,
                    text=current,
                ),
            ),
            text=current,
            verification=VerificationResult(ok=True),
        )
        draft = ProjectDraft(
            path=source,
            original_text="[monster]\nkind = i_med\n",
            current_text=current,
            saved_text=current,
            change_ranges=((1, 2),),
        )
        item = PreviewItem(
            title="monster.ltx",
            source_path=source,
            plan=plan,
            generated=generated,
            kind="file",
            draft=draft,
        )
        preview = ProjectPatchPreview(
            project=Project(id="p1", name="Demo", root_dir=Path(".")),
            items=(item,),
        )
        dialog = ProjectPreviewDialog(preview)
        try:
            self.assertTrue(dialog.select_patch_path(relative))
            self.assertTrue(
                dialog.code.editor.markersAtLine(1)
                & (1 << _CHANGE_MARKER)
            )
            self.assertFalse(
                dialog.code.editor.markersAtLine(0)
                & (1 << _CHANGE_MARKER)
            )
        finally:
            dialog.force_close()

    def test_preview_maps_resolved_changes_to_winner_file_lines(self):
        relative = Path("configs/defines.ltx")
        source = Path("gamedata") / relative
        full_text = "\n".join(
            "line %d = %d" % (index, index)
            for index in range(1, 301)
        ) + "\n"
        plan = PatchPlan(
            source_path=source,
            target=PatchTarget(
                source_path=source,
                root_path=Path("gamedata/configs/system.ltx"),
            ),
            patch_mod="ACE Patch",
            patch_file_rel=relative,
        )
        generated = GeneratedPatch(
            plan=plan,
            files=(
                PatchFile(
                    relative_path=Path("gamedata") / relative,
                    text=full_text,
                ),
            ),
            text=full_text,
            verification=VerificationResult(ok=True),
        )
        resolved = ProjectResolvedDraft(
            root_path=Path("gamedata/configs/system.ltx"),
            title="[Res]system.ltx",
            original_text="[system]\nallow_repair = false\n",
            current_text="[system]\nallow_repair = true\n",
            saved_text="[system]\nallow_repair = true\n",
            line_sources=(
                None,
                LineSource(
                    kind="file",
                    display="GAME\\defines.ltx",
                    path=Path("gamedata/configs/defines.ltx"),
                    line=226,
                ),
            ),
            change_ranges=((1, 2),),
        )
        item = PreviewItem(
            title="defines.ltx",
            source_path=source,
            plan=plan,
            generated=generated,
            kind="resolved",
            resolved=resolved,
        )
        preview = ProjectPatchPreview(
            project=Project(id="p1", name="Demo", root_dir=Path(".")),
            items=(item,),
        )
        dialog = ProjectPreviewDialog(preview)
        try:
            self.assertTrue(dialog.select_patch_path(relative))
            self.assertTrue(
                dialog.code.editor.markersAtLine(225)
                & (1 << _CHANGE_MARKER)
            )
            self.assertFalse(
                dialog.code.editor.markersAtLine(224)
                & (1 << _CHANGE_MARKER)
            )
        finally:
            dialog.force_close()

    def test_close_is_visual_and_keeps_instance(self):
        preview = _preview(
            ((Path("configs/a.ltx"), "[a]\nx = 1\n"),)
        )
        dialog = ProjectPreviewDialog(preview)
        try:
            dialog.show()
            self.assertTrue(dialog.isVisible())

            dialog.close()
            self.assertFalse(dialog.isVisible())
            self.assertFalse(dialog._allow_close)

            dialog.show()
            self.assertTrue(dialog.isVisible())
        finally:
            dialog.force_close()


class _ReuseController(ProjectControllerMixin):
    def __init__(self, project, forest, dialog):
        self.window = object()
        self.project = SimpleNamespace(
            active=project,
            has_changes=lambda: True,
        )
        self._forest = forest
        self._patch_preview_dialog = dialog
        self._patch_preview_stale = False
        self._patch_preview_snapshot = "SNAP"
        self.operations = SimpleNamespace(is_active=lambda _key: False)
        self.presented = []
        self.autosaved = False

    def has_forest(self):
        return self._forest is not None

    def _present_patch_preview(self):
        self.presented.append(True)
        return True

    def autosave_project(self):
        self.autosaved = True

    def set_status(self, *_args, **_kwargs):
        pass

    def log(self, _message):
        pass


class PatchPreviewReuseTest(unittest.TestCase):
    def test_reopen_without_invalidation_reuses_window(self):
        preview = _preview(((Path("configs/a.ltx"), "[a]\n"),))
        dialog = SimpleNamespace(preview=preview)
        forest = SimpleNamespace(cache_id="SNAP")
        controller = _ReuseController(preview.project, forest, dialog)

        controller.build_patch_preview()

        self.assertEqual(controller.presented, [True])
        self.assertFalse(controller.autosaved)

    def test_invalidation_marks_live_preview_stale(self):
        api = object.__new__(PlatformApi)
        api._patch_preview_dialog = object()
        api._patch_preview_stale = False
        api._project_prebuild_roots = None
        api.workspace = SimpleNamespace(
            roots_for=lambda _paths: {Path("configs")}
        )
        api.services = SimpleNamespace(
            cache_control=SimpleNamespace(
                invalidate_paths=lambda _roots: None,
            )
        )
        api.log = lambda _message: None

        api._invalidate_paths([Path("configs/a.ltx")])

        self.assertTrue(api._patch_preview_stale)


if __name__ == "__main__":
    unittest.main()
