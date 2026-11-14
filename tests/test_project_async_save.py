# -*- coding: utf-8 -*-
"""Фоновая запись текстовых черновиков проекта."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.support import SRC  # noqa: F401

from ui.platform_api import PlatformApi
from tests.test_platform_api import (
    FakeCommands,
    FakeEvents,
    FakeWindow,
)


class DeferredTasks:
    """Задача не выполняется, пока тест явно не завершит её."""

    def __init__(self):
        self.jobs = []

    def run(self, fn, on_done=None, on_log=None):
        self.jobs.append((fn, on_done))
        return object()

    def complete_next(self):
        fn, done = self.jobs.pop(0)
        try:
            result = fn(None)
        except Exception as exc:
            done(None, exc)
        else:
            done(result, None)


class AsyncProjectSaveTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.tasks = DeferredTasks()
        paths = SimpleNamespace(
            projects_dir=root / "projects",
            app_dir=root,
        )
        self.api = PlatformApi(
            FakeEvents(),
            FakeCommands(),
            self.tasks,
            None,
            paths,
        )
        self.api.window = FakeWindow()
        self.api.project.create("proj")
        self.source = root / "configs" / "text" / "rus" / "ru.xml"
        self.source.parent.mkdir(parents=True)
        self.source.write_text("OLD", encoding="utf-8")

    def test_same_file_is_written_by_one_worker_and_last_text_wins(self):
        calls = []

        def first(ok, error):
            calls.append(("first", ok, error))

        def second(ok, error):
            calls.append(("second", ok, error))

        self.assertTrue(
            self.api.write_project_text_async(
                self.source,
                "FIRST",
                on_done=first,
                original_text="OLD",
            )
        )
        self.assertTrue(
            self.api.write_project_text_async(
                self.source,
                "SECOND",
                on_done=second,
            )
        )

        # Второй текст ещё не запускал отдельный поток: он ждёт в
        # очереди, пока первый воркер не закончит запись.
        self.assertEqual(len(self.tasks.jobs), 1)
        draft = self.api.project.get_draft(self.source)
        self.assertEqual(draft.current_text, "FIRST")
        self.assertTrue(draft.is_dirty)

        self.tasks.complete_next()
        self.assertEqual(calls, [("first", True, None)])
        self.assertEqual(len(self.tasks.jobs), 1)
        self.assertEqual(
            self.api.project.get_draft(self.source).current_text,
            "SECOND",
        )

        self.tasks.complete_next()
        self.assertEqual(calls[-1], ("second", True, None))
        self.assertEqual(self.tasks.jobs, [])

        draft = self.api.project.get_draft(self.source)
        self.assertEqual(draft.current_text, "SECOND")
        self.assertEqual(draft.saved_text, "SECOND")
        self.assertFalse(draft.is_dirty)

    def test_tab_busy_flag_is_forwarded_to_editor(self):
        self.api.set_editor_tab_busy("tab.item", True)
        self.assertIn("tab.item", self.api.window.editor.tab_busy)

        self.api.set_editor_tab_busy("tab.item", False)
        self.assertNotIn("tab.item", self.api.window.editor.tab_busy)


if __name__ == "__main__":
    unittest.main()
