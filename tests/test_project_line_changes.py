# -*- coding: utf-8 -*-
"""Карта изменённых строк проекта: расчёт, запись и восстановление."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.support import SRC  # noqa: F401

from app.projects import ProjectService
from app.projects.line_changes import (
    changed_line_ranges,
    deserialize_change_map,
    expand_change_ranges,
    serialize_change_map,
)


class LineChangesTest(unittest.TestCase):
    def test_changed_ranges_cover_modified_and_inserted_lines(self):
        original = "one\ntwo\nthree\n"
        current = "one\ntwo changed\ninserted\nthree\n"

        ranges = changed_line_ranges(original, current)

        self.assertEqual(ranges, ((1, 3),))
        self.assertEqual(
            expand_change_ranges(ranges, 4),
            {1, 2},
        )

    def test_change_map_roundtrip_checks_revision(self):
        data = serialize_change_map(((1, 3), (7, 8)), 4)

        self.assertEqual(
            deserialize_change_map(data, 4),
            ((1, 3), (7, 8)),
        )
        self.assertIsNone(deserialize_change_map(data, 5))


class ProjectChangeMapPersistenceTest(unittest.TestCase):
    def test_autosave_persists_ranges_and_reopen_restores_them(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            service = ProjectService(root / "projects")
            project = service.create("Demo")
            source = root / "gamedata" / "configs" / "a.ltx"
            source.parent.mkdir(parents=True)
            source.write_text("one\ntwo\nthree\n", encoding="cp1251")

            service.update_draft(
                source,
                "one\ntwo changed\nthree\n",
            )
            service.autosave()

            draft = service.get_draft(source)
            self.assertEqual(draft.change_ranges, ((1, 2),))

            service.close()
            service.load(project.id)
            restored = service.get_draft(source)
            self.assertEqual(restored.change_ranges, ((1, 2),))
            service.close()

    def test_legacy_draft_without_map_is_upgraded_on_load(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            service = ProjectService(root / "projects")
            project = service.create("Demo")
            source = root / "gamedata" / "configs" / "a.ltx"
            source.parent.mkdir(parents=True)
            source.write_text("one\ntwo\nthree\n", encoding="cp1251")
            service.update_draft(source, "one\nchanged\nthree\n")
            service.autosave()
            service.close()

            draft_file = next(
                (project.root_dir / "drafts").glob("*.json")
            )
            data = json.loads(draft_file.read_text(encoding="utf-8"))
            data.pop("change_map", None)
            draft_file.write_text(
                json.dumps(data, ensure_ascii=False),
                encoding="utf-8",
            )

            service.load(project.id)
            upgraded = json.loads(
                draft_file.read_text(encoding="utf-8")
            )
            self.assertEqual(upgraded["change_map"]["ranges"], [[1, 2]])
            service.close()

    def test_reopen_uses_persisted_map_without_recomputing_diff(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            service = ProjectService(root / "projects")
            project = service.create("Demo")
            source = root / "gamedata" / "configs" / "a.ltx"
            source.parent.mkdir(parents=True)
            source.write_text("one\ntwo\nthree\n", encoding="cp1251")
            service.update_draft(source, "one\nchanged\nthree\n")
            service.autosave()
            service.close()

            with mock.patch(
                "app.projects.service.changed_line_ranges",
                side_effect=AssertionError("diff must not run"),
            ):
                service.load(project.id)

            restored = service.get_draft(source)
            self.assertEqual(restored.change_ranges, ((1, 2),))
            service.close()


if __name__ == "__main__":
    unittest.main()
