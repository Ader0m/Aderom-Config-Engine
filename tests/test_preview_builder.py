# -*- coding: utf-8 -*-
"""Предпросмотр как сценарий app: снимок, кэш и идентичность проекта."""

import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from app.patching.preview import PreviewSnapshot, ProjectPreviewBuilder
from app.projects import (
    Project,
    ProjectAssetDraft,
    ProjectDraft,
    ProjectResolvedDraft,
    ProjectService,
)
from app.projects.assets import ProjectAssetRegistry
from app.projects.readers import ProjectFileReaderRegistry


class _Services:
    """Заглушка core: для пустого проекта engine не вызывается."""


class _Forest:
    cache_id = "snapshot-1"
    scan_dir = "C:/game/gamedata/configs"
    files = ()
    xml_files = ()


def _project(root: Path) -> Project:
    return Project(
        id="demo",
        name="Demo",
        root_dir=root,
        modules=[],
        save_mode="root",
        xml_patch_mode="xml",
        created_at="2026-01-01T00:00:00",
        module_settings={"test.mode": "merge"},
    )


def _builder() -> ProjectPreviewBuilder:
    return ProjectPreviewBuilder(
        ProjectAssetRegistry(),
        ProjectFileReaderRegistry(),
    )


class PreviewSnapshotTest(unittest.TestCase):
    def test_copies_only_changed_drafts(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            project = _project(root)
            changed = ProjectDraft(
                path=root / "a.ltx",
                original_text="a = 1\n",
                current_text="a = 2\n",
                saved_text="a = 2\n",
            )
            unchanged = ProjectDraft(
                path=root / "b.ltx",
                original_text="b = 1\n",
                current_text="b = 1\n",
                saved_text="b = 1\n",
            )
            project.drafts = {
                "a": changed,
                "b": unchanged,
            }
            project.assets = {
                "c": ProjectAssetDraft(
                    path=root / "c.ogg",
                    original_sha1="",
                    current_bytes=b"data",
                    saved_bytes=b"",
                )
            }
            project.resolved_drafts = {
                "r": ProjectResolvedDraft(
                    root_path=root / "configs",
                    title="root",
                    original_text="x = 1\n",
                    current_text="x = 2\n",
                    saved_text="x = 2\n",
                    tab_id="tab",
                )
            }

            snapshot = PreviewSnapshot.capture(
                project,
                save_mode="root",
                xml_patch_mode="xml",
                module_settings={"test.mode": "merge"},
            )

            self.assertIs(snapshot.project, project)
            self.assertEqual(snapshot.project_id, "demo")
            self.assertEqual(snapshot.project_name, "Demo")
            self.assertEqual(snapshot.save_mode, "root")
            self.assertEqual(snapshot.xml_patch_mode, "xml")
            self.assertEqual(
                snapshot.module_settings,
                {"test.mode": "merge"},
            )
            self.assertEqual(len(snapshot.drafts), 1)
            self.assertIsNot(snapshot.drafts[0], changed)
            self.assertEqual(snapshot.drafts[0].current_text, "a = 2\n")
            self.assertEqual(len(snapshot.assets), 1)
            self.assertEqual(len(snapshot.resolved_drafts), 1)
            self.assertIsNot(
                snapshot.resolved_drafts[0],
                project.resolved_drafts["r"],
            )


class ProjectPreviewBuilderTest(unittest.TestCase):
    def test_empty_project_returns_empty_preview(self):
        with tempfile.TemporaryDirectory() as d:
            project = _project(Path(d))
            builder = _builder()

            preview = builder.build(
                _Services(),
                _Forest(),
                PreviewSnapshot.capture(
                    project,
                    save_mode="root",
                    xml_patch_mode="xml",
                    module_settings={},
                ),
            )

            self.assertIs(preview.project, project)
            self.assertEqual(preview.items, ())

    def test_invalidate_clears_cache(self):
        builder = _builder()
        builder._cached_preview_items(("key",), lambda: ("value",))
        self.assertTrue(builder._preview_cache)

        builder.invalidate()

        self.assertFalse(builder._preview_cache)
        self.assertEqual(builder._preview_cache_chars, 0)


class ServicePreviewBuilderTest(unittest.TestCase):
    def test_service_build_preview_keeps_project_identity(self):
        with tempfile.TemporaryDirectory() as d:
            service = ProjectService(Path(d) / "projects")
            project = service.create("Empty")

            preview = service.build_preview(_Services(), _Forest())

            self.assertIs(preview.project, project)
            self.assertEqual(preview.items, ())

    def test_service_preview_rejects_other_project(self):
        with tempfile.TemporaryDirectory() as d:
            service = ProjectService(Path(d) / "projects")
            first = service.create("First")
            service.create("Second")

            preview = service.build_preview(
                _Services(),
                _Forest(),
                expected_project=first,
            )

            self.assertIsNone(preview)

    def test_service_invalidate_preview_cache(self):
        with tempfile.TemporaryDirectory() as d:
            service = ProjectService(Path(d) / "projects")
            service.create("Empty")
            builder = service.preview_builder
            builder._cached_preview_items(("key",), lambda: ("value",))

            service.invalidate_preview_cache()

            self.assertFalse(builder._preview_cache)


if __name__ == "__main__":
    unittest.main()
