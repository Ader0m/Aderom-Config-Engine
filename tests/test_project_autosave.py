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

    def test_remove_does_not_touch_other_project(self):
        old = self.service.create("Old")
        source = self._dirty(old.root_dir, "a.ltx")
        original_key = self.service.path_key
        switched = [False]

        def key(path):
            if not switched[0]:
                # Смена проекта между _require_active() и state-lock.
                switched[0] = True
                self.service.create("New")
            return original_key(path)

        self.service.path_key = key
        self.addCleanup(delattr, self.service, "path_key")

        self.assertFalse(self.service.remove_draft(source))
        self.assertIsNotNone(old.draft(source))

    def _remove_while_write_lock_held(self, callback):
        import threading

        self.service._write_lock.acquire()
        try:
            result = {}

            def worker():
                result["removed"] = callback()

            thread = threading.Thread(target=worker)
            thread.start()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertTrue(result["removed"])
        finally:
            self.service._write_lock.release()

    def test_remove_drafts_does_not_wait_for_write_lock(self):
        project = self.service.create("NoWait")
        source = self._dirty(project.root_dir, "a.ltx")
        asset = project.root_dir / "sounds" / "b.ogg"
        asset.parent.mkdir(parents=True)
        self.service.update_asset_draft(asset, b"OGG")
        root = project.root_dir / "configs"
        root.mkdir(parents=True, exist_ok=True)
        self.service.update_resolved_draft(
            "tab",
            root,
            "ROOT",
            "[a]\nx = 1\n",
            "[a]\nx = 2\n",
        )
        self.service.autosave()

        text_target = self.service.draft_store.text_target(
            project.root_dir,
            source,
        )
        asset_target = self.service.draft_store.asset_target(
            project.root_dir,
            asset,
        )
        resolved_target = self.service.draft_store.resolved_target(
            project.root_dir,
            self.service.get_resolved_draft("tab"),
        )
        for target in (text_target, asset_target, resolved_target):
            self.assertTrue(target.is_file())

        for callback in (
            lambda: self.service.remove_draft(source),
            lambda: self.service.remove_asset_draft(asset),
            lambda: self.service.remove_resolved_draft("tab"),
        ):
            with self.subTest(callback=callback):
                self._remove_while_write_lock_held(callback)

        # Удаления отложены, а не потеряны: flush разбирает очередь.
        self.service.flush()
        for target in (text_target, asset_target, resolved_target):
            self.assertFalse(target.is_file())

    def test_staged_write_does_not_resurrect_removed_draft(self):
        project = self.service.create("Staged")
        source = project.root_dir / "configs" / "a.ltx"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("[a]\nx = 0\n", encoding="cp1251")
        target, payload, revision, _saved_at = (
            self.service.stage_text_draft(source, "[a]\nx = 1\n")
        )

        self.assertTrue(self.service.remove_draft(source))
        self.assertFalse(
            self.service.write_staged_draft(
                source,
                target,
                payload,
                revision,
            )
        )
        self.assertFalse(target.is_file())

    def test_concurrent_update_after_remove_keeps_fresh_draft(self):
        import threading
        import time

        project = self.service.create("Concurrent")
        source = self._dirty(project.root_dir, "a.ltx")
        self.service.autosave()
        target = self.service.draft_store.text_target(
            project.root_dir,
            source,
        )
        self.assertTrue(target.is_file())

        removed = {}
        self.service._write_lock.acquire()
        try:
            def remove_worker():
                removed["value"] = self.service.remove_draft(source)

            remove_thread = threading.Thread(target=remove_worker)
            remove_thread.start()
            deadline = time.monotonic() + 2
            while (
                not self.service.has_pending_cleanup()
                and remove_thread.is_alive()
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            self.assertTrue(self.service.has_pending_cleanup())

            def update_worker():
                self.service.update_draft(source, "[a]\nx = 2\n")

            update_thread = threading.Thread(target=update_worker)
            update_thread.start()
            update_thread.join(2)
            self.assertFalse(update_thread.is_alive())
            self.assertFalse(self.service.has_pending_cleanup())
        finally:
            self.service._write_lock.release()

        remove_thread.join(2)
        self.assertFalse(remove_thread.is_alive())
        self.assertTrue(removed["value"])
        self.service.autosave()
        self.assertTrue(target.is_file())
        self.assertEqual(self.service.get_text(source), "[a]\nx = 2\n")

    def test_close_without_active_project_drains_pending_deletes(self):
        target = self.root / "stale-draft.json"
        target.write_text("{}", encoding="utf-8")
        self.service._queue_deferred_delete(target)
        self.service.session.deactivate()

        self.service.close()

        self.assertFalse(target.is_file())
        self.assertFalse(self.service.has_pending_cleanup())

    def test_recreated_draft_cancels_deferred_delete(self):
        project = self.service.create("Recreated")
        source = self._dirty(project.root_dir, "a.ltx")
        self.service.autosave()
        target = self.service.draft_store.text_target(
            project.root_dir,
            source,
        )
        self.assertTrue(target.is_file())

        self._remove_while_write_lock_held(
            lambda: self.service.remove_draft(source)
        )
        self.assertTrue(self.service.has_pending_cleanup())

        # Тот же путь снова стал черновиком: отложенное удаление старого
        # файла больше не должно выполняться.
        self.service.update_draft(source, "[a]\nx = 2\n")
        self.assertFalse(self.service.has_pending_cleanup())
        self.service._drain_pending_deletes()
        self.assertTrue(target.is_file())


if __name__ == "__main__":
    unittest.main()
