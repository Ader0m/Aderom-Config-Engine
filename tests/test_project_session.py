# -*- coding: utf-8 -*-
"""Сессия проекта: снимок состояния и единственный писатель."""

import json
import tempfile
import threading
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest import mock

from tests.support import SRC  # noqa: F401

from app.projects import Project, ProjectError, ProjectService
from app.projects.session import ProjectSession


def _project(root: Path, **overrides) -> Project:
    values = {
        "id": "demo",
        "name": "Demo",
        "root_dir": root,
        "modules": ["base"],
        "save_mode": "root",
        "xml_patch_mode": "xml",
        "created_at": "2026-01-01T00:00:00",
        "module_settings": {"test.mode": "merge"},
    }
    values.update(overrides)
    return Project(**values)


class ProjectStateTest(unittest.TestCase):
    def test_state_is_immutable_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            project = _project(Path(d))
            session = ProjectSession()
            session.activate(project)

            state = session.state()

            self.assertEqual(state.id, "demo")
            self.assertEqual(state.name, "Demo")
            self.assertEqual(state.modules, ("base",))
            self.assertEqual(
                state.module_settings,
                {"test.mode": "merge"},
            )
            with self.assertRaises(FrozenInstanceError):
                state.name = "Other"
            with self.assertRaises(TypeError):
                state.module_settings["other"] = 1

            project.name = "Changed"
            project.module_settings["test.mode"] = "override"
            project.modules.append("extra")

            self.assertEqual(state.name, "Demo")
            self.assertEqual(state.module_settings["test.mode"], "merge")
            self.assertEqual(state.modules, ("base",))
            fresh = session.state()
            self.assertEqual(fresh.name, "Changed")
            self.assertEqual(
                fresh.module_settings["test.mode"],
                "override",
            )

    def test_empty_session_has_no_state(self):
        session = ProjectSession()
        self.assertFalse(session.is_active)
        self.assertIsNone(session.state())
        self.assertIsNone(session.active)


class ProjectSessionRevisionTest(unittest.TestCase):
    def test_revision_grows_on_mutations_only(self):
        with tempfile.TemporaryDirectory() as d:
            project = _project(Path(d))
            session = ProjectSession()

            self.assertEqual(session.revision, 0)
            session.activate(project)
            self.assertEqual(session.revision, 1)
            self.assertEqual(session.state().revision, 1)

            self.assertEqual(session.read(lambda item: item.name), "Demo")
            self.assertEqual(session.revision, 1)

            session.write(lambda item: setattr(item, "name", "New"))
            self.assertEqual(session.revision, 2)

            session.deactivate()
            self.assertEqual(session.revision, 3)
            session.deactivate()
            self.assertEqual(session.revision, 3)

    def test_failed_command_keeps_state_and_revision(self):
        with tempfile.TemporaryDirectory() as d:
            session = ProjectSession()
            session.activate(_project(Path(d)))

            def fail(project):
                raise ValueError("bad command")

            with self.assertRaises(ValueError):
                session.write(fail)

            self.assertEqual(session.revision, 1)
            self.assertEqual(session.active.name, "Demo")

    def test_commands_require_active_project(self):
        session = ProjectSession()
        for call in (
            lambda: session.require_active(),
            lambda: session.read(lambda project: project.name),
            lambda: session.write(lambda project: None),
        ):
            with self.assertRaises(ProjectError):
                call()


class ProjectSessionConcurrencyTest(unittest.TestCase):
    def test_concurrent_writes_are_serialized(self):
        with tempfile.TemporaryDirectory() as d:
            project = _project(Path(d), module_settings={})
            session = ProjectSession()
            session.activate(project)
            workers = 4
            per_worker = 250
            start = threading.Barrier(workers)
            errors = []

            def bump(_project):
                settings = _project.module_settings
                settings["count"] = settings.get("count", 0) + 1

            def worker():
                try:
                    start.wait(timeout=5)
                    for _ in range(per_worker):
                        session.write(bump)
                except Exception as exc:  # noqa: BLE001 - поток теста
                    errors.append(exc)

            threads = [
                threading.Thread(target=worker) for _ in range(workers)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20)

            self.assertEqual(errors, [])
            self.assertEqual(
                project.module_settings["count"],
                workers * per_worker,
            )
            self.assertEqual(session.revision, 1 + workers * per_worker)


class ProjectServiceSessionTest(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.base = Path(self._temp.name) / "projects"
        self.service = ProjectService(self.base)

    def test_active_is_read_only(self):
        project = self.service.create("Demo")

        with self.assertRaises(AttributeError):
            self.service.active = project

        self.assertIs(self.service.active, project)
        self.assertTrue(self.service.is_active)

    def test_mode_and_settings_go_through_session(self):
        project = self.service.create("Demo")
        revision = self.service.session.revision

        self.service.set_patch_mode("on_file")
        self.assertEqual(self.service.session.revision, revision + 1)
        self.assertEqual(self.service.patch_mode(), "on_file")

        self.service.set_xml_patch_mode("xml_override")
        self.assertEqual(self.service.session.revision, revision + 2)

        self.service.set_module_setting("test.mode", "override")
        self.assertEqual(self.service.session.revision, revision + 3)
        self.assertEqual(self.service.module_setting("test.mode"), "override")

        meta = json.loads(
            (project.root_dir / "project.json").read_text(encoding="utf-8")
        )
        self.assertEqual(meta["save_mode"], "on_file")
        self.assertEqual(meta["xml_patch_mode"], "xml_override")
        self.assertEqual(
            meta["module_settings"],
            {"test.mode": "override"},
        )

    def test_status_text_uses_snapshot(self):
        self.service.create("Snapshot")
        self.assertIn("Проект: Snapshot", self.service.status_text())
        self.assertIn("режим: root", self.service.status_text())

        self.service.set_patch_mode("on_file")
        self.assertIn("режим: on_file", self.service.status_text())

    def test_close_deactivates_project(self):
        self.service.create("Closing")
        revision = self.service.session.revision

        self.service.close()

        self.assertFalse(self.service.is_active)
        self.assertIsNone(self.service.active)
        self.assertEqual(self.service.session.revision, revision + 1)

    def test_module_settings_copy_does_not_mutate_project(self):
        self.service.create("Settings")
        self.service.set_module_setting("a", 1)

        values = self.service.module_settings()
        values["a"] = 2
        values["b"] = 3

        self.assertEqual(self.service.module_setting("a"), 1)
        self.assertEqual(self.service.module_setting("b", "none"), "none")


class ProjectServiceRollbackTest(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.base = Path(self._temp.name) / "projects"
        self.service = ProjectService(self.base)

    def _fail_write(self):
        return mock.patch.object(
            self.service.repository,
            "write_meta",
            side_effect=OSError("disk full"),
        )

    def _meta(self, project):
        return json.loads(
            (project.root_dir / "project.json").read_text(encoding="utf-8")
        )

    def test_set_patch_mode_rolls_back_on_write_error(self):
        project = self.service.create("Rollback")
        revision = self.service.session.revision

        with self._fail_write():
            with self.assertRaises(OSError):
                self.service.set_patch_mode("on_file")

        self.assertEqual(project.save_mode, "root")
        self.assertEqual(self.service.patch_mode(), "root")
        self.assertEqual(self.service.session.revision, revision)
        self.assertEqual(self._meta(project)["save_mode"], "root")

    def test_set_xml_patch_mode_rolls_back_on_write_error(self):
        project = self.service.create("Rollback")
        revision = self.service.session.revision

        with self._fail_write():
            with self.assertRaises(OSError):
                self.service.set_xml_patch_mode("xml_override")

        self.assertEqual(project.xml_patch_mode, "xml")
        state = self.service.session.state()
        self.assertEqual(state.xml_patch_mode, "xml")
        self.assertEqual(self.service.session.revision, revision)
        self.assertEqual(
            self._meta(project)["xml_patch_mode"],
            "xml",
        )

    def test_module_setting_existing_value_rolls_back(self):
        project = self.service.create("Rollback")
        self.service.set_module_setting("a", 1)
        revision = self.service.session.revision

        with self._fail_write():
            with self.assertRaises(OSError):
                self.service.set_module_setting("a", 2)

        self.assertEqual(project.module_settings["a"], 1)
        self.assertEqual(self.service.module_setting("a"), 1)
        self.assertEqual(self.service.session.revision, revision)
        self.assertEqual(self._meta(project)["module_settings"], {"a": 1})

    def test_module_setting_new_key_rolls_back(self):
        project = self.service.create("Rollback")
        revision = self.service.session.revision

        with self._fail_write():
            with self.assertRaises(OSError):
                self.service.set_module_setting("new", 1)

        self.assertNotIn("new", project.module_settings)
        self.assertEqual(
            self.service.module_setting("new", "missing"),
            "missing",
        )
        self.assertEqual(self.service.session.revision, revision)

    def test_module_setting_none_rolls_back_to_none(self):
        project = self.service.create("Rollback")
        self.service.set_module_setting("mode", None)
        revision = self.service.session.revision

        with self._fail_write():
            with self.assertRaises(OSError):
                self.service.set_module_setting("mode", 2)

        self.assertIn("mode", project.module_settings)
        self.assertIsNone(project.module_settings["mode"])
        self.assertIsNone(self.service.module_setting("mode", "missing"))
        self.assertEqual(self.service.session.revision, revision)

    def test_create_does_not_activate_on_write_error(self):
        with self._fail_write():
            with self.assertRaises(OSError):
                self.service.create("Broken")

        self.assertFalse(self.service.is_active)
        self.assertIsNone(self.service.active)
        self.assertEqual(self.service.session.revision, 0)


if __name__ == "__main__":
    unittest.main()
