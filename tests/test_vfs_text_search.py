# -*- coding: utf-8 -*-
"""Полнотекстовый поиск по VFS: ядро и окно результатов."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.support import SRC  # noqa: F401

from core.tools.text_search import (
    TextSearchCancelled,
    search_text_files,
)
from tests.qt_support import qapp
from ui.operations import OperationRegistry
from ui.platform.vfs_search_actions import VfsSearchAction
from ui.vfs_search import VfsSearchDialog

_app = qapp()


class TextSearchFilesTest(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name) / "configs"
        self.root.mkdir()

    def _write(self, relative, text, encoding="utf-8"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding=encoding)
        return path

    def test_finds_literal_in_text_files_only(self):
        ltx = self._write(
            "a.ltx",
            "[section]\nvalue = Needle\nNeedle again\n",
        )
        xml = self._write("sub/b.xml", "<root>needle</root>\n")
        script = self._write("sub/c.script", "local x = 'NEEDLE'\n")
        lua = self._write("d.lua", "print('needle')\n")
        self._write("binary.dds", "needle", encoding="cp1251")

        result = search_text_files(self.root, "needle")

        self.assertEqual(result.files_scanned, 4)
        self.assertEqual(result.files_matched, 4)
        self.assertEqual(result.total_matches, 5)
        by_path = {match.path: match for match in result.matches}
        self.assertEqual(by_path[ltx].matches, 2)
        self.assertEqual(by_path[ltx].first_line, 2)
        self.assertEqual(by_path[ltx].snippet, "value = Needle")
        self.assertEqual(by_path[xml].first_line, 1)
        self.assertEqual(by_path[script].matches, 1)
        self.assertEqual(by_path[lua].matches, 1)

    def test_case_sensitive_option(self):
        self._write("a.ltx", "needle\nNeedle\n")

        insensitive = search_text_files(self.root, "needle")
        sensitive = search_text_files(
            self.root,
            "needle",
            case_sensitive=True,
        )

        self.assertEqual(insensitive.total_matches, 2)
        self.assertEqual(sensitive.total_matches, 1)

    def test_overlay_replaces_and_adds_files(self):
        existing = self._write("a.ltx", "[a]\nold = 1\n")
        outside = Path(self._temp.name) / "outside.ltx"
        outside.write_text("needle", encoding="utf-8")

        result = search_text_files(
            self.root,
            "needle",
            source_files={
                existing: "[a]\nnew = needle\n",
                self.root / "new" / "b.ltx": "needle draft\n",
                outside: "needle outside\n",
            },
        )

        self.assertEqual(result.files_matched, 2)
        by_name = {
            match.relative_path.replace("\\", "/"): match
            for match in result.matches
        }
        self.assertEqual(by_name["a.ltx"].first_line, 2)
        self.assertEqual(by_name["new/b.ltx"].first_line, 1)
        self.assertNotIn("outside.ltx", by_name)

    def test_cp1251_text_is_read(self):
        self._write("a.ltx", "[секция]\nзначение = игла\n", encoding="cp1251")

        result = search_text_files(self.root, "игла")

        self.assertEqual(result.files_matched, 1)
        self.assertEqual(result.matches[0].snippet, "значение = игла")

    def test_tree_service_searches_scan_dir(self):
        from core.services.tree import TreeServices

        self._write("a.ltx", "needle\n")
        service = TreeServices(None, None)

        result = service.search_text(
            SimpleNamespace(scan_dir=self.root),
            "needle",
        )

        self.assertEqual(result.files_matched, 1)
        self.assertEqual(result.matches[0].relative_path, "a.ltx")

    def test_cancellation_stops_the_search(self):
        self._write("a.ltx", "needle\n")
        checks = {"count": 0}

        def cancelled():
            checks["count"] += 1
            return checks["count"] > 1

        with self.assertRaises(TextSearchCancelled):
            search_text_files(
                self.root,
                "needle",
                cancel_check=cancelled,
            )


class VfsSearchDialogTest(unittest.TestCase):
    def _dialog(self, searched, opened):
        dialog = VfsSearchDialog(
            on_search=lambda query, case: searched.append((query, case)),
            on_open=lambda match, query: opened.append((match, query)),
        )
        self.addCleanup(dialog.close)
        return dialog

    def test_search_button_passes_query_and_case_flag(self):
        searched = []
        dialog = self._dialog(searched, [])
        dialog.query_edit.setText("  needle  ")
        dialog.case_check_box.setChecked(True)

        dialog.search_button.click()

        self.assertEqual(searched, [("needle", True)])
        self.assertFalse(dialog.search_button.isEnabled())
        self.assertFalse(dialog.open_button.isEnabled())
        self.assertIn("Поиск: needle", dialog.info_label.text())

    def test_results_are_shown_and_opened_with_result_query(self):
        opened = []
        dialog = self._dialog([], opened)
        match = SimpleNamespace(
            path=Path("configs/a.ltx"),
            relative_path="a.ltx",
            matches=2,
            first_line=3,
            snippet="needle here",
        )
        result = SimpleNamespace(
            query="needle",
            matches=(match,),
            files_scanned=10,
            files_matched=1,
            total_matches=2,
        )

        dialog.show_results(result, elapsed=0.25)
        dialog._open_current()

        self.assertTrue(dialog.search_button.isEnabled())
        self.assertEqual(dialog.tree.topLevelItemCount(), 1)
        item = dialog.tree.topLevelItem(0)
        self.assertEqual(
            [item.text(column) for column in range(4)],
            ["a.ltx", "2", "3", "needle here"],
        )
        self.assertIn("файлов проверено: 10", dialog.info_label.text().lower())
        self.assertEqual(opened, [(match, "needle")])


class VfsSearchActionTest(unittest.TestCase):
    class _Api:
        def __init__(self, forest):
            self.workspace = SimpleNamespace(forest=forest)
            self.operations = OperationRegistry()
            self.window = None
            self.logs = []
            self.statuses = []
            self.errors = []
            self.open_requests = []
            self.window_specs = []

        def project_overlay(self):
            return SimpleNamespace(
                source_files={Path("configs/a.ltx"): "draft"},
                revision_token="overlay-1",
            )

        def log(self, message):
            self.logs.append(str(message))

        def set_status(self, message, timeout=0):
            self.statuses.append(str(message))

        def show_error(self, message):
            self.errors.append(str(message))

        def cancel_operation(self, key):
            return False

        def request_open_file_raw_with_search(self, path, query):
            self.open_requests.append((path, query))
            return True

        def open_window(self, spec, widget):
            self.window_specs.append((spec, widget))
            return True

    class _Tasks:
        def __init__(self, operations):
            self.operations = operations
            self.runs = []

        def run(self, fn, **kwargs):
            self.runs.append((fn, kwargs))
            return object()

    class _Services:
        def __init__(self):
            self.calls = []
            self.tree = SimpleNamespace(search_text=self.search_text)

        def search_text(self, forest, query, **kwargs):
            self.calls.append((forest, query, kwargs))
            return "result"

    def test_search_passes_overlay_to_service_and_uses_operation_key(self):
        forest = SimpleNamespace(cache_id="forest-1", scan_dir=Path("configs"))
        api = self._Api(forest)
        tasks = self._Tasks(api.operations)
        services = self._Services()
        action = VfsSearchAction(api, services, tasks)

        action._search("Needle", False)

        self.assertEqual(len(tasks.runs), 1)
        _fn, kwargs = tasks.runs[0]
        self.assertTrue(kwargs["operation_key"].startswith("vfs.search:0:"))
        self.assertEqual(kwargs["title"], "Поиск по VFS: Needle")
        _fn(lambda _message: None)
        self.assertEqual(len(services.calls), 1)
        _forest, query, call_kwargs = services.calls[0]
        self.assertIs(_forest, forest)
        self.assertEqual(query, "Needle")
        self.assertEqual(
            call_kwargs["source_files"],
            {Path("configs/a.ltx"): "draft"},
        )
        self.assertFalse(call_kwargs["case_sensitive"])
        self.assertIn("Поиск по VFS: Needle…", api.statuses)

    def test_open_match_uses_search_navigation(self):
        forest = SimpleNamespace(cache_id="forest-1", scan_dir=Path("configs"))
        api = self._Api(forest)
        action = VfsSearchAction(
            api,
            self._Services(),
            self._Tasks(api.operations),
        )
        match = SimpleNamespace(
            path=Path("configs/a.ltx"),
            relative_path="a.ltx",
            first_line=4,
        )

        action._open_match(match, "needle")

        self.assertEqual(
            api.open_requests,
            [(Path("configs/a.ltx"), "needle")],
        )
        self.assertIn("Открытие: a.ltx…", api.statuses)

    def test_show_without_tree_reports_error(self):
        api = self._Api(None)
        action = VfsSearchAction(
            api,
            self._Services(),
            self._Tasks(api.operations),
        )

        self.assertFalse(action.show())
        self.assertIn(
            "Дерево не построено: дождитесь окончания сборки.",
            api.errors,
        )

    def test_show_opens_managed_window(self):
        forest = SimpleNamespace(cache_id="forest-1", scan_dir=Path("configs"))
        api = self._Api(forest)
        action = VfsSearchAction(
            api,
            self._Services(),
            self._Tasks(api.operations),
        )

        self.assertTrue(action.show())

        self.assertEqual(len(api.window_specs), 1)
        spec, widget = api.window_specs[0]
        self.assertEqual(spec.key, action.WINDOW_KEY)
        self.assertEqual(spec.title, "Поиск по VFS")
        self.assertIs(widget, action._dialog)


if __name__ == "__main__":
    unittest.main()
