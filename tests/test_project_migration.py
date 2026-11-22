# -*- coding: utf-8 -*-
"""One-time migration проекта: backup, сохранность данных, идемпотентность."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.support import SRC  # noqa: F401

import app.projects.repository as repository_module
from app.projects import PROJECT_SCHEMA_VERSION, ProjectError, ProjectService


class ProjectMigrationTest(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.base = Path(self._temp.name) / "projects"
        self.service = ProjectService(self.base)

    # -------------------------------------------------- помощники
    def _create_project_with_data(self):
        project = self.service.create(
            "Legacy",
            modules=["base"],
            save_mode="root",
        )
        source = Path(self._temp.name) / "configs" / "system.ltx"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("[system]\nversion = 1\n", encoding="windows-1251")

        self.service.update_draft(source, "[system]\nversion = 2\n")
        resolved_root = Path(self._temp.name) / "gamedata" / "configs"
        resolved_root.mkdir(parents=True, exist_ok=True)
        self.service.update_resolved_draft(
            "resolved:system",
            resolved_root,
            "SYSTEM",
            "[system]\nversion = 2\n",
            "[system]\nversion = 3\n",
        )
        asset = Path(self._temp.name) / "sounds" / "test.ogg"
        asset.parent.mkdir(parents=True, exist_ok=True)
        self.service.update_asset_draft(asset, b"OGG-BYTES")
        self.service.autosave()

        self.service.set_module_setting("test.mode", "merge")
        self.service.save_tabs(
            [
                {
                    "tab_id": "tab-1",
                    "title": "system.ltx",
                    "kind": "editor",
                    "active": True,
                }
            ]
        )
        self.service.close()
        return project, source, asset

    def _downgrade_meta(self, project_dir, **overrides):
        meta_path = project_dir / "project.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta.pop("schema_version", None)
        meta.update(overrides)
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False),
            encoding="utf-8",
        )
        return meta_path

    def _backups(self, project_dir):
        return sorted(project_dir.glob(".migration_backup_*"))

    # -------------------------------------------------- тесты
    def test_legacy_project_migrates_and_preserves_data(self):
        project, source, asset = self._create_project_with_data()
        meta_path = self._downgrade_meta(
            project.root_dir,
            save_mode="in_place",
        )
        legacy_bytes = meta_path.read_bytes()

        loaded = self.service.load(project.id)

        self.assertEqual(loaded.save_mode, "on_file")
        self.assertEqual(loaded.xml_patch_mode, "xml")
        self.assertEqual(loaded.name, "Legacy")
        self.assertEqual(loaded.modules, ["base"])

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        self.assertEqual(meta["schema_version"], PROJECT_SCHEMA_VERSION)
        self.assertEqual(meta["save_mode"], "on_file")

        self.assertEqual(
            self.service.get_text(source),
            "[system]\nversion = 2\n",
        )
        self.assertEqual(
            self.service.get_resolved_text("resolved:system"),
            "[system]\nversion = 3\n",
        )
        self.assertEqual(self.service.get_asset_bytes(asset), b"OGG-BYTES")
        self.assertEqual(self.service.module_setting("test.mode"), "merge")
        tabs = self.service.load_tabs()
        self.assertEqual(len(tabs), 1)
        self.assertEqual(tabs[0]["tab_id"], "tab-1")

        backups = self._backups(project.root_dir)
        self.assertEqual(len(backups), 1)
        backup = backups[0]
        # Backup хранит исходный проект.json байт-в-байт.
        self.assertEqual((backup / "project.json").read_bytes(), legacy_bytes)
        self.assertTrue((backup / "drafts").is_dir())
        self.assertTrue((backup / "resolved").is_dir())
        self.assertTrue((backup / "assets").is_dir())
        self.assertTrue((backup / "tabs.json").is_file())

    def test_migration_is_idempotent(self):
        project, _source, _asset = self._create_project_with_data()
        self._downgrade_meta(project.root_dir, save_mode="in_place")

        self.service.load(project.id)
        self.service.close()
        self.service.load(project.id)

        self.assertEqual(len(self._backups(project.root_dir)), 1)
        meta = json.loads(
            (project.root_dir / "project.json").read_text(encoding="utf-8")
        )
        self.assertEqual(meta["schema_version"], PROJECT_SCHEMA_VERSION)

    def test_failed_write_keeps_project_and_backup(self):
        project, _source, _asset = self._create_project_with_data()
        meta_path = self._downgrade_meta(
            project.root_dir,
            save_mode="in_place",
        )
        legacy_bytes = meta_path.read_bytes()

        with mock.patch.object(
            repository_module,
            "write_json_atomic",
            side_effect=OSError("disk full"),
        ):
            with self.assertRaises(ProjectError) as ctx:
                self.service.load(project.id)

        self.assertIn("мигрированный проект", str(ctx.exception))
        self.assertEqual(meta_path.read_bytes(), legacy_bytes)
        self.assertEqual(len(self._backups(project.root_dir)), 1)

    def test_unknown_save_mode_aborts_before_migration(self):
        project, _source, _asset = self._create_project_with_data()
        meta_path = self._downgrade_meta(
            project.root_dir,
            save_mode="broken",
        )
        legacy_bytes = meta_path.read_bytes()

        with self.assertRaises(ProjectError):
            self.service.load(project.id)

        self.assertEqual(meta_path.read_bytes(), legacy_bytes)
        self.assertEqual(self._backups(project.root_dir), [])

    def test_current_schema_load_creates_no_backup(self):
        project, _source, _asset = self._create_project_with_data()

        self.service.load(project.id)

        self.assertEqual(self._backups(project.root_dir), [])

    def test_missing_required_field_keeps_project_and_active(self):
        first, source, _asset = self._create_project_with_data()
        broken = self.service.create("Broken")
        self.service.close()
        meta_path = broken.root_dir / "project.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta.pop("save_mode")
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False),
            encoding="utf-8",
        )
        broken_bytes = meta_path.read_bytes()

        self.service.load(first.id)
        self.service.update_draft(source, "[system]\nversion = 9\n")

        with self.assertRaises(ProjectError) as ctx:
            self.service.load(broken.id)

        self.assertIn("save_mode", str(ctx.exception))
        self.assertEqual(meta_path.read_bytes(), broken_bytes)
        self.assertEqual(self._backups(broken.root_dir), [])
        self.assertIsNotNone(self.service.active)
        self.assertEqual(self.service.active.id, first.id)
        self.assertEqual(
            self.service.get_text(source),
            "[system]\nversion = 9\n",
        )

    def test_failed_load_keeps_active_project(self):
        first, source, _asset = self._create_project_with_data()
        second = self.service.create("Future")
        self.service.close()
        meta_path = second.root_dir / "project.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["schema_version"] = PROJECT_SCHEMA_VERSION + 1
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False),
            encoding="utf-8",
        )

        self.service.load(first.id)
        self.service.update_draft(source, "[system]\nversion = 9\n")

        with self.assertRaises(ProjectError):
            self.service.load(second.id)

        self.assertIsNotNone(self.service.active)
        self.assertEqual(self.service.active.id, first.id)
        self.assertEqual(
            self.service.get_text(source),
            "[system]\nversion = 9\n",
        )


if __name__ == "__main__":
    unittest.main()
