# -*- coding: utf-8 -*-
"""Хранилище project.json: схема, атомарная запись, чтение."""

import json
import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from app.projects import PROJECT_SCHEMA_VERSION, Project, ProjectError
from app.projects.repository import (
    LEGACY_SCHEMA_VERSION,
    ProjectRepository,
    write_json_atomic,
)


def _project(root_dir: Path) -> Project:
    return Project(
        id="demo",
        name="Demo",
        root_dir=root_dir,
        modules=["base"],
        save_mode="on_file",
        xml_patch_mode="xml",
        created_at="2026-01-01T00:00:00",
        module_settings={"test.mode": "merge"},
    )


class ProjectRepositoryWriteTest(unittest.TestCase):
    def test_write_meta_records_current_schema(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "demo"
            repository = ProjectRepository()

            repository.write_meta(_project(root))

            meta = json.loads(
                (root / "project.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                meta["schema_version"],
                PROJECT_SCHEMA_VERSION,
            )
            self.assertEqual(meta["id"], "demo")
            self.assertEqual(meta["name"], "Demo")
            self.assertEqual(meta["modules"], ["base"])
            self.assertEqual(meta["save_mode"], "on_file")
            self.assertEqual(meta["xml_patch_mode"], "xml")
            self.assertEqual(meta["module_settings"], {"test.mode": "merge"})
            self.assertEqual(meta["created_at"], "2026-01-01T00:00:00")

    def test_write_meta_creates_project_dir(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "nested" / "demo"
            ProjectRepository().write_meta(_project(root))
            self.assertTrue((root / "project.json").is_file())

    def test_atomic_write_leaves_no_temporary_files(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "data.json"
            write_json_atomic(target, {"ok": True})
            self.assertEqual(
                list(Path(d).glob("*.tmp.*")),
                [],
            )
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                {"ok": True},
            )


class ProjectRepositoryReadTest(unittest.TestCase):
    def test_missing_project_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ProjectError) as ctx:
                ProjectRepository().read_meta(Path(d) / "missing")
            self.assertIn("Проект не найден", str(ctx.exception))

    def test_corrupted_json_raises(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "demo"
            root.mkdir()
            (root / "project.json").write_text("{", encoding="utf-8")
            with self.assertRaises(ProjectError) as ctx:
                ProjectRepository().read_meta(root)
            self.assertIn("Не удалось прочитать проект", str(ctx.exception))

    def test_non_object_json_raises(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "demo"
            root.mkdir()
            (root / "project.json").write_text("[]", encoding="utf-8")
            with self.assertRaises(ProjectError):
                ProjectRepository().read_meta(root)

    def test_schema_version_defaults_to_legacy(self):
        self.assertEqual(
            ProjectRepository.schema_version({}),
            LEGACY_SCHEMA_VERSION,
        )
        self.assertEqual(
            ProjectRepository.schema_version({"schema_version": "2"}),
            PROJECT_SCHEMA_VERSION,
        )
        self.assertEqual(
            ProjectRepository.schema_version({"schema_version": "bad"}),
            LEGACY_SCHEMA_VERSION,
        )


class ProjectRepositoryValidationTest(unittest.TestCase):
    def _meta(self, **overrides):
        meta = {
            "id": "demo",
            "name": "Demo",
            "save_mode": "root",
            "xml_patch_mode": "xml",
        }
        meta.update(overrides)
        return meta

    def test_valid_meta_passes(self):
        self.assertIsNone(
            ProjectRepository.validate_meta(self._meta())
        )

    def test_missing_required_field_is_reported(self):
        for field in (
            "id",
            "name",
            "save_mode",
            "xml_patch_mode",
        ):
            meta = self._meta()
            meta.pop(field)
            with self.assertRaises(ProjectError) as ctx:
                ProjectRepository.validate_meta(meta)
            self.assertIn(field, str(ctx.exception))

    def test_non_string_field_is_rejected(self):
        for field in (
            "id",
            "name",
            "save_mode",
            "xml_patch_mode",
        ):
            with self.assertRaises(ProjectError) as ctx:
                ProjectRepository.validate_meta(self._meta(**{field: 5}))
            self.assertIn(field, str(ctx.exception))

    def test_empty_id_and_name_are_rejected(self):
        for field in ("id", "name"):
            with self.assertRaises(ProjectError):
                ProjectRepository.validate_meta(self._meta(**{field: "  "}))


class ProjectRepositoryMigrationGuardTest(unittest.TestCase):
    def test_current_schema_is_not_migrated(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "demo"
            repository = ProjectRepository()
            repository.write_meta(_project(root))
            meta = repository.read_meta(root)

            result = repository.ensure_current(root, meta)

            self.assertIs(result, meta)
            self.assertEqual(
                list(root.glob(".migration_backup_*")),
                [],
            )

    def test_newer_schema_is_rejected_without_changes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "demo"
            root.mkdir()
            target = root / "project.json"
            target.write_text(
                json.dumps(
                    {
                        "schema_version": PROJECT_SCHEMA_VERSION + 1,
                        "id": "demo",
                        "name": "Demo",
                        "save_mode": "root",
                        "xml_patch_mode": "xml",
                    }
                ),
                encoding="utf-8",
            )
            before = target.read_bytes()

            with self.assertRaises(ProjectError) as ctx:
                ProjectRepository().ensure_current(
                    root,
                    ProjectRepository().read_meta(root),
                )

            self.assertIn("более новой версией", str(ctx.exception))
            self.assertEqual(target.read_bytes(), before)
            self.assertEqual(
                list(root.glob(".migration_backup_*")),
                [],
            )


if __name__ == "__main__":
    unittest.main()
