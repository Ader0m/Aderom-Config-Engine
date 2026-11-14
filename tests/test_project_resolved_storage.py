# -*- coding: utf-8 -*-
"""Компактное хранение resolved-черновиков проекта."""

import json
import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.models import LineSource
from app.projects import ProjectResolvedDraft, ProjectService


class ResolvedDraftStorageTest(unittest.TestCase):
    def _service(self, directory):
        service = ProjectService(Path(directory) / "projects")
        project = service.create("Storage")
        return service, project

    def _draft_file(self, project):
        files = list((project.root_dir / "resolved").glob("*.json"))
        self.assertEqual(len(files), 1)
        return files[0]

    def test_line_sources_are_deduplicated_and_compact(self):
        with tempfile.TemporaryDirectory() as d:
            service, project = self._service(d)
            source_a = LineSource(
                kind="file",
                display="a.ltx",
                path=Path(d) / "a.ltx",
                line=1,
            )
            source_b = LineSource(
                kind="file",
                display="b.ltx",
                path=Path(d) / "b.ltx",
                line=2,
            )
            text = "\n".join("x = %d" % index for index in range(10000))
            sources = tuple(
                (None, source_a, source_b)[index % 3]
                for index in range(10000)
            )

            service.update_resolved_draft(
                "resolved:storage",
                Path(d) / "system.ltx",
                "RESOLVED: system.ltx",
                text,
                text,
                line_sources=sources,
            )
            service.save_resolved_draft("resolved:storage")

            target = self._draft_file(project)
            payload = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], 3)
            self.assertEqual(len(payload["line_sources"]["sources"]), 2)
            self.assertNotIsInstance(payload["line_sources"], list)
            # Крупный текст сжат, а original == current не дублируется.
            self.assertIsInstance(payload["current_text"], dict)
            self.assertNotIn("original_text", payload)
            # Старый формат для этого текста весил бы несколько мегабайт.
            self.assertLess(target.stat().st_size, 250_000)

            project_id = project.id
            service.close()
            service.load(project_id)
            draft = service.get_resolved_draft("resolved:storage")
            self.assertEqual(len(draft.line_sources), 10000)
            self.assertIsNone(draft.line_sources[0])
            self.assertIs(draft.line_sources[1], draft.line_sources[4])
            self.assertEqual(draft.line_sources[1].display, "a.ltx")

    def test_saved_text_is_restored_when_omitted(self):
        with tempfile.TemporaryDirectory() as d:
            service, project = self._service(d)
            text = "[a]\nx = 1\n"
            service.update_resolved_draft(
                "resolved:system",
                Path(d) / "system.ltx",
                "RESOLVED: system.ltx",
                text,
                text,
                line_sources=(None, None, None),
            )
            service.save_resolved_draft("resolved:system")

            payload = json.loads(
                self._draft_file(project).read_text(encoding="utf-8")
            )
            self.assertNotIn("saved_text", payload)

            project_id = project.id
            service.close()
            service.load(project_id)
            draft = service.get_resolved_draft("resolved:system")
            self.assertEqual(draft.saved_text, text)
            self.assertFalse(draft.is_dirty)

    def test_legacy_line_sources_are_read_and_share_objects(self):
        with tempfile.TemporaryDirectory() as d:
            service, project = self._service(d)
            source = LineSource(
                kind="file",
                display="a.ltx",
                path=Path(d) / "a.ltx",
                line=3,
            )
            legacy = {
                "tab_id": "resolved:legacy",
                "root_path": str(Path(d) / "system.ltx"),
                "title": "RESOLVED: legacy",
                "original_text": "[a]\nx = 1\n",
                "current_text": "[a]\nx = 2\n",
                "saved_text": "[a]\nx = 2\n",
                "line_sources": [
                    None,
                    {
                        "kind": source.kind,
                        "display": source.display,
                        "path": str(source.path),
                        "line": source.line,
                        "open_raw": source.open_raw,
                    },
                    {
                        "kind": source.kind,
                        "display": source.display,
                        "path": str(source.path),
                        "line": source.line,
                        "open_raw": source.open_raw,
                    },
                ],
            }
            (project.root_dir / "resolved" / "legacy.json").write_text(
                json.dumps(legacy),
                encoding="utf-8",
            )

            loaded = service.load(project.id)
            self.assertEqual(len(loaded.resolved_drafts), 1)
            draft = service.get_resolved_draft("resolved:legacy")
            self.assertIsNone(draft.line_sources[0])
            self.assertIs(draft.line_sources[1], draft.line_sources[2])

            # При следующем сохранении старый формат мигрирует в v3.
            service.save_resolved_draft("resolved:legacy")
            payload = json.loads(
                (project.root_dir / "resolved" / "legacy.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertNotIsInstance(payload["line_sources"], list)
            self.assertEqual(payload["version"], 3)


class CorruptedResolvedDraftTest(unittest.TestCase):
    def test_duplicated_current_text_is_collapsed_on_load(self):
        with tempfile.TemporaryDirectory() as d:
            service = ProjectService(Path(d) / "projects")
            project = service.create("Corrupted")
            text = "[a]\nx = 1\n"
            legacy = {
                "version": 2,
                "tab_id": "resolved:corrupted",
                "root_path": str(Path(d) / "system.ltx"),
                "title": "RESOLVED",
                "original_text": text,
                "current_text": text + text,
                "line_sources": {"version": 2, "sources": [], "ids": ""},
            }
            (project.root_dir / "resolved" / "corrupted.json").write_text(
                json.dumps(legacy),
                encoding="utf-8",
            )

            service.load(project.id)
            draft = service.get_resolved_draft("resolved:corrupted")
            self.assertEqual(draft.current_text, text)
            self.assertEqual(draft.saved_text, text)
            self.assertFalse(draft.is_dirty)


class ResolvedDraftKeyTest(unittest.TestCase):
    def test_empty_tab_id_roundtrip_uses_same_key(self):
        with tempfile.TemporaryDirectory() as d:
            service = ProjectService(Path(d) / "projects")
            service.create("Keys")
            root = Path(d) / "system.ltx"

            draft = service.update_resolved_draft(
                "",
                root,
                "RESOLVED",
                "[a]\nx = 1\n",
                "[a]\nx = 2\n",
            )
            key = ProjectService.resolved_key("", root)
            self.assertEqual(service.resolved_key("", root), key)
            self.assertIs(service.active.resolved_drafts[key], draft)
            self.assertIs(service.get_resolved_draft(key), draft)

            service.save_resolved_draft(key)
            self.assertFalse(draft.is_dirty)
            self.assertTrue(service.remove_resolved_draft(key))
            self.assertIsNone(service.get_resolved_draft(key))


if __name__ == "__main__":
    unittest.main()
