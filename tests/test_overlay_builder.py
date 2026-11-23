# -*- coding: utf-8 -*-
"""OverlayBuilder: снимок черновиков и кэш по ревизиям."""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests.support import SRC  # noqa: F401

from app.projects import Project, ProjectDraft, ProjectResolvedDraft
from app.projects.overlay import OverlayBuilder
from app.projects.service import ProjectService


def _project(root: Path, **kwargs) -> Project:
    values = {
        "id": "demo",
        "name": "Demo",
        "root_dir": root,
        "drafts": {},
        "resolved_drafts": {},
    }
    values.update(kwargs)
    return Project(**values)


class OverlayBuilderTest(unittest.TestCase):
    def test_empty_project_returns_empty_overlay(self):
        with tempfile.TemporaryDirectory() as d:
            overlay = OverlayBuilder().build(_project(Path(d)))

            self.assertEqual(overlay.source_files, {})
            self.assertEqual(overlay.resolved_edits, {})
            self.assertEqual(overlay.new_files, frozenset())

    def test_maps_drafts_resolved_and_new_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / "configs" / "a.ltx"
            draft = ProjectDraft(
                path=source,
                original_text="[a]\nx = 1\n",
                current_text="[a]\nx = 2\n",
                saved_text="[a]\nx = 2\n",
            )
            new_file = ProjectDraft(
                path=root / "configs" / "b.ltx",
                original_text="",
                current_text="[b]\n",
                saved_text="[b]\n",
                is_new=True,
            )
            resolved = ProjectResolvedDraft(
                root_path=root / "gamedata" / "configs",
                title="ROOT",
                original_text="x = 1\n",
                current_text="x = 2\n",
                saved_text="x = 2\n",
                tab_id="tab",
                line_sources=(None,),
            )
            project = _project(
                root,
                drafts={"a": draft, "b": new_file},
                resolved_drafts={"tab": resolved},
            )

            overlay = OverlayBuilder().build(project)

            self.assertEqual(
                overlay.source_files[source],
                "[a]\nx = 2\n",
            )
            self.assertIn(new_file.path, overlay.new_files)
            self.assertEqual(
                overlay.resolved_edits[resolved.root_path],
                ("x = 1\n", "x = 2\n", (None,)),
            )
            self.assertTrue(overlay.revision_token)

    def test_cache_reuses_overlay_until_revision_changes(self):
        with tempfile.TemporaryDirectory() as d:
            service = ProjectService(Path(d) / "projects")
            project = service.create("Overlay")
            source = Path(d) / "configs" / "a.ltx"
            source.parent.mkdir(parents=True)
            source.write_text("[a]\nx = 1\n", encoding="cp1251")
            service.update_draft(source, "[a]\nx = 2\n")

            first = service.project_overlay()
            second = service.project_overlay()
            self.assertIs(first, second)

            service.update_draft(source, "[a]\nx = 3\n")
            third = service.project_overlay()

            self.assertIsNot(third, first)
            self.assertNotEqual(
                third.revision_token,
                first.revision_token,
            )
            self.assertEqual(
                third.source_files[Path(source)],
                "[a]\nx = 3\n",
            )

    def test_clear_drops_cached_overlay(self):
        with tempfile.TemporaryDirectory() as d:
            service = ProjectService(Path(d) / "projects")
            service.create("Overlay")
            source = Path(d) / "configs" / "a.ltx"
            source.parent.mkdir(parents=True)
            source.write_text("[a]\nx = 1\n", encoding="cp1251")
            service.update_draft(source, "[a]\nx = 2\n")

            first = service.project_overlay()
            service.overlay_builder.clear()
            second = service.project_overlay()

            self.assertIsNot(first, second)
            self.assertEqual(
                first.source_files,
                second.source_files,
            )
            self.assertNotEqual(
                first.revision_token,
                second.revision_token,
            )

    def test_recreated_text_draft_updates_overlay(self):
        with tempfile.TemporaryDirectory() as d:
            service = ProjectService(Path(d) / "projects")
            service.create("Overlay")
            source = Path(d) / "configs" / "a.ltx"
            source.parent.mkdir(parents=True)
            source.write_text("[a]\nx = 1\n", encoding="cp1251")
            service.update_draft(source, "[a]\nx = 2\n")
            first = service.project_overlay()

            service.remove_draft(source)
            service.update_draft(source, "[a]\nx = 3\n")
            second = service.project_overlay()

            self.assertIsNot(first, second)
            self.assertEqual(second.source_files[source], "[a]\nx = 3\n")
            self.assertNotEqual(
                first.revision_token,
                second.revision_token,
            )

    def test_recreated_resolved_draft_updates_overlay(self):
        with tempfile.TemporaryDirectory() as d:
            service = ProjectService(Path(d) / "projects")
            service.create("Overlay")
            root = Path(d) / "gamedata" / "configs"
            root.mkdir(parents=True)
            service.update_resolved_draft(
                "tab",
                root,
                "ROOT",
                "[a]\nx = 1\n",
                "[a]\nx = 2\n",
            )
            first = service.project_overlay()

            service.remove_resolved_draft("tab")
            service.update_resolved_draft(
                "tab",
                root,
                "ROOT",
                "[a]\nx = 1\n",
                "[a]\nx = 3\n",
            )
            second = service.project_overlay()

            self.assertIsNot(first, second)
            self.assertEqual(
                second.resolved_edits[root][1],
                "[a]\nx = 3\n",
            )

    def test_collapsed_resolved_text_invalidates_overlay(self):
        with tempfile.TemporaryDirectory() as d:
            service = ProjectService(Path(d) / "projects")
            service.create("Overlay")
            root = Path(d) / "gamedata" / "configs"
            root.mkdir(parents=True)
            service.update_resolved_draft(
                "tab",
                root,
                "ROOT",
                "[a]\nx = 1\n",
                "[a]\nx = 2\n",
            )
            service.project_overlay()

            draft = service.get_resolved_draft("tab")
            draft.current_text = "[a]\nx = 2\n" * 2
            service.save_resolved_draft("tab")

            overlay = service.project_overlay()
            self.assertEqual(
                overlay.resolved_edits[root][1],
                "[a]\nx = 2\n",
            )

    def test_no_project_returns_empty_overlay(self):
        self.assertEqual(
            OverlayBuilder().build(None),
            OverlayBuilder().build(None),
        )

    def test_stale_project_is_rejected_by_is_current(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            draft = ProjectDraft(
                path=root / "configs" / "a.ltx",
                original_text="[a]\nx = 1\n",
                current_text="[a]\nx = 2\n",
                saved_text="[a]\nx = 2\n",
            )
            project = _project(root, drafts={"a": draft})
            builder = OverlayBuilder()
            first = builder.build(project)

            stale = builder.build(
                project,
                is_current=lambda _project: False,
            )

            self.assertEqual(stale.source_files, {})
            self.assertNotEqual(stale.revision_token, first.revision_token)
            fresh = builder.build(project)
            self.assertEqual(
                fresh.source_files,
                first.source_files,
            )

    def test_restore_draft_invalidates_cached_overlay(self):
        with tempfile.TemporaryDirectory() as d:
            service = ProjectService(Path(d) / "projects")
            service.create("Overlay")
            source = Path(d) / "configs" / "a.ltx"
            source.parent.mkdir(parents=True)
            source.write_text("[a]\nx = 1\n", encoding="cp1251")
            service.update_draft(source, "[a]\nx = 2\n")
            snapshot = replace(service.get_draft(source))
            service.project_overlay()

            service.update_draft(source, "[a]\nx = 3\n")
            second = service.project_overlay()
            service.restore_draft(snapshot)
            third = service.project_overlay()

            self.assertEqual(third.source_files[source], "[a]\nx = 2\n")
            self.assertIsNot(third, second)


if __name__ == "__main__":
    unittest.main()
