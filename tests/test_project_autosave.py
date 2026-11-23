# -*- coding: utf-8 -*-
"""Автосохранение: привязка к проекту, эпоха и отсутствие stale-записи."""

import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from app.projects import PreviewItem, ProjectService
from app.projects.use_cases import rollback_preview_item


class AutosaveProjectBindingTest(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.service = ProjectService(self.root / "projects")

    def _dirty(self, project_root, name, text="[a]\nx = 1\n"):
        source = Path(project_root) / "configs" / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("[a]\nx = 0\n", encoding="cp1251")
        self.service.update_draft(source, text)
        return source

    def test_expected_project_mismatch_writes_nothing(self):
        first = self.service.create("First")
        self._dirty(first.root_dir, "a.ltx")

        second = self.service.create("Second")
        other = self._dirty(second.root_dir, "b.ltx")

        saved = self.service.autosave(expected_project=first)

        self.assertEqual(saved, [])
        self.assertTrue(self.service.get_draft(other).is_dirty)

    def test_epoch_stops_stale_pass_after_first_write(self):
        project = self.service.create("Epoch")
        first = self._dirty(project.root_dir, "a.ltx")
        second = self._dirty(project.root_dir, "b.ltx")

        original = self.service.draft_store.write_payload
        calls = []

        def hook(target, payload):
            calls.append(target)
            if len(calls) == 1:
                self.service.invalidate_autosave()
            return original(target, payload)

        self.service.draft_store.write_payload = hook
        try:
            saved = self.service.autosave()
        finally:
            self.service.draft_store.write_payload = original

        # Первый файл успел записаться, второй проход отверг по эпохе.
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(saved), 1)
        self.assertTrue(self.service.get_draft(second).is_dirty)

        self.service.flush()
        self.assertFalse(self.service.get_draft(first).is_dirty)
        self.assertFalse(self.service.get_draft(second).is_dirty)

    def test_close_invalidates_epoch_and_flushes(self):
        project = self.service.create("Closing")
        source = self._dirty(project.root_dir, "a.ltx")
        before = self.service._autosave_epoch

        self.service.close()

        self.assertGreater(self.service._autosave_epoch, before)
        self.assertIsNone(self.service.get_draft(source))

    def _remove_on_first_write(self, callback):
        original = self.service.draft_store.write_payload
        calls = []

        def hook(target, payload):
            calls.append(target)
            if len(calls) == 1:
                callback()
            return original(target, payload)

        self.service.draft_store.write_payload = hook
        self.addCleanup(
            setattr,
            self.service.draft_store,
            "write_payload",
            original,
        )
        return calls

    def test_removed_text_draft_is_not_resurrected(self):
        project = self.service.create("Text")
        first = self._dirty(project.root_dir, "a.ltx")
        second = self._dirty(project.root_dir, "b.ltx")
        second_target = self.service.draft_store.text_target(
            project.root_dir,
            second,
        )

        self._remove_on_first_write(
            lambda: self.service.remove_draft(second)
        )
        saved = self.service.autosave()

        self.assertEqual([event.path for event in saved], [first])
        self.assertFalse(second_target.is_file())
        self.assertIsNone(self.service.get_draft(second))

    def test_removed_asset_draft_is_not_resurrected(self):
        project = self.service.create("Asset")
        first = self._dirty(project.root_dir, "a.ltx")
        asset = project.root_dir / "sounds" / "b.ogg"
        asset.parent.mkdir(parents=True)
        self.service.update_asset_draft(asset, b"OGG")
        asset_target = self.service.draft_store.asset_target(
            project.root_dir,
            asset,
        )

        self._remove_on_first_write(
            lambda: self.service.remove_asset_draft(asset)
        )
        saved = self.service.autosave()

        self.assertEqual([event.path for event in saved], [first])
        self.assertFalse(asset_target.is_file())
        self.assertIsNone(self.service.get_asset_draft(asset))

    def test_removed_resolved_draft_is_not_resurrected(self):
        project = self.service.create("Resolved")
        first = self._dirty(project.root_dir, "a.ltx")
        root = project.root_dir / "configs"
        root.mkdir(parents=True, exist_ok=True)
        self.service.update_resolved_draft(
            "tab",
            root,
            "ROOT",
            "[a]\nx = 1\n",
            "[a]\nx = 2\n",
        )
        draft = self.service.get_resolved_draft("tab")
        target = self.service.draft_store.resolved_target(
            project.root_dir,
            draft,
        )

        self._remove_on_first_write(
            lambda: self.service.remove_resolved_draft("tab")
        )
        saved = self.service.autosave()

        self.assertEqual([event.path for event in saved], [first])
        self.assertFalse(target.is_file())
        self.assertIsNone(self.service.get_resolved_draft("tab"))

    def test_rollback_during_autosave_does_not_resurrect(self):
        project = self.service.create("Rollback")
        first = self._dirty(project.root_dir, "a.ltx")
        second = self._dirty(project.root_dir, "b.ltx")
        second_target = self.service.draft_store.text_target(
            project.root_dir,
            second,
        )
        item = PreviewItem(
            title="b.ltx",
            source_path=second,
            plan=None,
            generated=None,
            rollback_paths=(second,),
        )

        self._remove_on_first_write(
            lambda: rollback_preview_item(self.service, item)
        )
        saved = self.service.autosave()

        self.assertEqual([event.path for event in saved], [first])
        self.assertFalse(second_target.is_file())
        self.assertIsNone(self.service.get_draft(second))

    def test_remove_does_not_touch_closed_project(self):
        import threading

        old = self.service.create("Old")
        source = self._dirty(old.root_dir, "a.ltx")
        ready = threading.Event()
        original_key = self.service.path_key

        def key(path):
            ready.set()
            return original_key(path)

        self.service.path_key = key
        self.addCleanup(delattr, self.service, "path_key")
        result = {}

        def worker():
            result["removed"] = self.service.remove_draft(source)

        # Держим write-lock, пока worker ждёт его, и меняем проект.
        self.service._write_lock.acquire()
        try:
            thread = threading.Thread(target=worker)
            thread.start()
            self.assertTrue(ready.wait(timeout=5))
            self.service.create("New")
        finally:
            self.service._write_lock.release()

        thread.join(timeout=10)
        self.assertFalse(result["removed"])
        self.assertIsNotNone(old.draft(source))


if __name__ == "__main__":
    unittest.main()
