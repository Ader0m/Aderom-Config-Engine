# -*- coding: utf-8 -*-
"""Фоновая фиксация правок resolved-документа."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import SRC  # noqa: F401

from tests.qt_support import qapp
from core.patching.ltx.document import parse_ltx_document as real_parse
from ui.bus import EventBus
from ui.editor_tabs import EditorTabs
from ui.platform_api import PlatformApi

_app = qapp()


class _StatusBar:
    def showMessage(self, *_args, **_kwargs):
        pass


class _Window:
    def __init__(self):
        self.editor = EditorTabs(events=EventBus())
        self.logs = []

    def log(self, message):
        self.logs.append(str(message))

    def log_status(self, *_args, **_kwargs):
        pass

    def statusBar(self):
        return _StatusBar()

    def setWindowTitle(self, *_args):
        pass

    def set_project_status(self, *_args):
        pass

    def _update_project_menu(self):
        pass


class _Services:
    def invalidate_resolver_cache(self):
        return None


class _InlineTasks:
    def run(self, fn, on_done=None, on_log=None):
        try:
            result = fn(lambda _message: None)
        except Exception as exc:
            if on_done is not None:
                on_done(None, exc)
            return None
        if on_done is not None:
            on_done(result, None)
        return None


class _DeferredTasks:
    def __init__(self):
        self.jobs = []

    def run(self, fn, on_done=None, on_log=None):
        self.jobs.append((fn, on_done))
        return None

    def flush(self):
        jobs, self.jobs = self.jobs, []
        for fn, on_done in jobs:
            try:
                result = fn(lambda _message: None)
            except Exception as exc:
                if on_done is not None:
                    on_done(None, exc)
                continue
            if on_done is not None:
                on_done(result, None)


class ResolvedAsyncEditTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.events = EventBus()
        self.commands = SimpleNamespace(register=lambda *a, **k: None)
        paths = SimpleNamespace(
            projects_dir=root / "projects",
            app_dir=root,
        )
        self.tasks = _InlineTasks()
        self.api = PlatformApi(
            self.events,
            self.commands,
            self.tasks,
            _Services(),
            paths,
        )
        self.window = _Window()
        self.api.window = self.window
        self.api.project.create("async", save_mode="root")
        self.api._forest = object()

        self.tab_id = "base.resolved:system.ltx"
        self.root = root / "system.ltx"
        self.api.open_resolved_tab(
            self.tab_id,
            "[Res]system.ltx",
            "[item]\nkind = i_food\ncost = 10\n",
            (None, None, None),
            self.root,
        )
        self.document = self.api.resolved_document(self.tab_id)

    def tearDown(self):
        self.window.editor.close()

    def test_async_edit_updates_document_and_callback(self):
        result = []
        self.assertTrue(
            self.api.apply_resolved_field_edit_async(
                self.tab_id,
                "item",
                "kind",
                "i_med",
                on_done=lambda ok, error: result.append((ok, error)),
            )
        )

        self.assertIn("kind = i_med", self.document.current_text)
        self.assertIn("kind = i_med", self.window.editor.get_tab(
            self.tab_id
        ).text())
        self.assertEqual(result, [(True, None)])
        self.assertIsNotNone(self.document.parsed_document())

    def test_second_edit_reuses_parsed_document(self):
        with patch(
            "app.documents.parse_ltx_document",
            side_effect=real_parse,
        ) as parser:
            self.api.apply_resolved_field_edit_async(
                self.tab_id,
                "item",
                "kind",
                "i_med",
            )
            first = parser.call_count
            self.assertGreaterEqual(first, 1)

            self.api.apply_resolved_field_edit_async(
                self.tab_id,
                "item",
                "kind",
                "i_heavy",
            )

            self.assertEqual(parser.call_count, first)

        self.assertIn("kind = i_heavy", self.document.current_text)

    def test_async_rename_updates_field_index(self):
        result = []
        self.assertTrue(
            self.api.rename_resolved_field_async(
                self.tab_id,
                "item",
                "kind",
                "item_kind",
                "i_med",
                on_done=lambda ok, error: result.append((ok, error)),
            )
        )

        self.assertIn("item_kind = i_med", self.document.current_text)
        self.assertEqual(self.document.field_line("item", "item_kind"), 2)
        self.assertEqual(self.document.field_line("item", "kind"), 0)
        self.assertEqual(result, [(True, None)])

    def test_platform_queue_serializes_edits(self):
        tasks = _DeferredTasks()
        self.api.tasks = tasks
        done = []

        self.api.apply_resolved_field_edit_async(
            self.tab_id,
            "item",
            "kind",
            "i_med",
            on_done=lambda ok, error: done.append(("kind", ok)),
        )
        self.api.apply_resolved_field_edit_async(
            self.tab_id,
            "item",
            "cost",
            "20",
            on_done=lambda ok, error: done.append(("cost", ok)),
        )

        self.assertEqual(len(tasks.jobs), 1)
        tasks.flush()
        self.api._pump_resolved_edit_queue(self.tab_id)

        self.assertEqual(len(tasks.jobs), 1)
        tasks.flush()

        self.assertIn("kind = i_med", self.document.current_text)
        self.assertIn("cost = 20", self.document.current_text)
        self.assertEqual(done, [("kind", True), ("cost", True)])

    def _open_owned_document(self):
        tab_id = "base.resolved:owned.ltx"
        root = Path(self._tmp.name) / "owned.ltx"
        self.api.open_resolved_tab(
            tab_id,
            "[Res]owned.ltx",
            "[item]\nkind = i_food\ncost = 10\n",
            (None, None, None),
            root,
            owner="mod_a",
        )
        return tab_id, self.api.resolved_document(tab_id)

    def test_foreign_owner_cannot_edit_resolved_document(self):
        tab_id, document = self._open_owned_document()
        before = document.current_text

        self.assertFalse(
            self.api.apply_resolved_line_edit(
                tab_id,
                "replace",
                2,
                "kind = i_med",
                owner="mod_b",
            )
        )
        self.assertFalse(
            self.api.apply_resolved_field_edit(
                tab_id,
                "item",
                "kind",
                "i_med",
                owner="mod_b",
            )
        )
        self.assertFalse(
            self.api.rename_resolved_field(
                tab_id,
                "item",
                "kind",
                "item_kind",
                "i_med",
                owner="mod_b",
            )
        )
        self.assertFalse(
            self.api.apply_resolved_field_edit_async(
                tab_id,
                "item",
                "kind",
                "i_med",
                owner="mod_b",
            )
        )
        self.assertFalse(
            self.api.rename_resolved_field_async(
                tab_id,
                "item",
                "kind",
                "item_kind",
                "i_med",
                owner="mod_b",
            )
        )
        self.assertFalse(
            self.api.undo_resolved_document(tab_id, owner="mod_b")
        )
        self.assertFalse(
            self.api.redo_resolved_document(tab_id, owner="mod_b")
        )
        self.assertFalse(
            self.api.update_resolved_draft(
                tab_id,
                document.root_path,
                document.title,
                document.original_text,
                "[item]\nkind = i_med\ncost = 10\n",
                document.line_sources,
                owner="mod_b",
            )
        )
        self.assertFalse(
            self.api.sync_resolved_tab(
                tab_id,
                "[item]\nkind = i_med\ncost = 10\n",
                document.line_sources,
                document.root_path,
                owner="mod_b",
            )
        )
        self.assertFalse(
            self.api.sync_resolved_line_edit(
                tab_id,
                SimpleNamespace(
                    action="replace",
                    line_number=2,
                    text="kind = i_med",
                ),
                owner="mod_b",
            )
        )
        self.assertFalse(
            self.api.ensure_resolved_document(
                tab_id,
                document.root_path,
                owner="mod_b",
            )
        )
        self.assertEqual(document.current_text, before)

        self.assertTrue(
            self.api.apply_resolved_field_edit(
                tab_id,
                "item",
                "kind",
                "i_med",
                owner="mod_a",
            )
        )
        self.assertIn("kind = i_med", document.current_text)
        self.assertTrue(
            self.api.undo_resolved_document(tab_id, owner="mod_a")
        )
        self.assertTrue(
            self.api.redo_resolved_document(tab_id, owner="mod_a")
        )

    def test_foreign_owner_cannot_retain_or_release_resolved_document(self):
        tab_id, _document = self._open_owned_document()

        self.assertFalse(
            self.api.retain_resolved_document(tab_id, owner="mod_b")
        )
        self.assertFalse(
            self.api.release_resolved_document(tab_id, owner="mod_b")
        )
        self.assertTrue(
            self.api.retain_resolved_document(tab_id, owner="mod_a")
        )
        self.assertTrue(
            self.api.release_resolved_document(tab_id, owner="mod_a")
        )

    def test_closed_tab_without_document_clears_owner(self):
        tab_id, document = self._open_owned_document()
        self.api._resolved_documents.pop(tab_id, None)
        self.window.editor.close_tab(tab_id)
        self.api._on_tab_closed(tab_id)

        self.api.open_resolved_tab(
            tab_id,
            "[Res]reused.ltx",
            "[item]\nkind = i_med\ncost = 5\n",
            (None, None, None),
            document.root_path,
            owner="mod_b",
        )

        self.assertIsNotNone(self.api.resolved_document(tab_id))
        self.assertTrue(
            self.api.apply_resolved_field_edit(
                tab_id,
                "item",
                "kind",
                "i_heavy",
                owner="mod_b",
            )
        )


if __name__ == "__main__":
    unittest.main()
