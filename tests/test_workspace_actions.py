# -*- coding: utf-8 -*-
"""Регрессия: workspace-действия работают с TaskService, а не с SDK API."""

import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from sdk.contracts import Events
from ui.operations import OperationRegistry, OperationSnapshot, RUNNING
from ui.platform.workspace_actions import WorkspaceActions


class _Events:
    def __init__(self):
        self.published = []
        self.subscriptions = []

    def subscribe(self, event, callback):
        self.subscriptions.append((event, callback))
        return lambda: None

    def publish(self, event, payload=None):
        self.published.append((event, payload))


class _Api:
    def __init__(self):
        self.logs = []
        self.statuses = []
        self.errors = []
        self.ready = []
        self._closing = False

    def log(self, message):
        self.logs.append(str(message))

    def set_status(self, message, timeout=None):
        self.statuses.append(str(message))

    def project_overlay(self):
        return None

    def ask_directory(self, _path=""):
        return ""

    def show_error(self, message):
        self.errors.append(str(message))

    def publish_tree_ready(self, forest):
        self.ready.append(forest)
        return object()


class _Tasks:
    def __init__(self, operations=None):
        self.operations = operations or OperationRegistry()
        self.runs = []

    def run(self, fn, **kwargs):
        self.runs.append((fn, kwargs))
        return object()


class _Forest:
    def __init__(self):
        self.cache_id = "forest-1"
        self.roots = [object(), object(), object()]
        self.total_files = 12
        self.system_tree_files = 5
        self.xml_files = [object(), object()]
        self.total_xml_files = 2
        self.resolver_sections = 42


class _Services:
    """Сервис без ``tree_summary``: сводку строит платформа."""

    def __init__(self):
        self.calls = []
        self.prebuild = self

    def start(self, forest, config_dir, overlay=None):
        self.calls.append((forest, config_dir, overlay))


class WorkspaceActionsTest(unittest.TestCase):
    def _actions(self, tasks):
        api = _Api()
        events = _Events()
        actions = WorkspaceActions(api, None, tasks, events)
        actions.config_dir = Path("configs")
        return actions, api, events

    def test_run_uses_task_service_operation_registry(self):
        tasks = _Tasks()
        actions, api, events = self._actions(tasks)

        actions.run()

        self.assertEqual(len(tasks.runs), 1)
        _fn, kwargs = tasks.runs[0]
        self.assertEqual(kwargs["operation_key"], "vfs.rebuild")
        self.assertEqual(kwargs["title"], "Пересборка VFS")
        self.assertIn(
            (Events.CLEAN_RUN_STARTED, str(Path("configs"))),
            events.published,
        )
        self.assertEqual(api.statuses, ["Сканирование…"])

    def test_run_does_not_start_duplicate_active_rebuild(self):
        operations = OperationRegistry()
        operations.register(
            OperationSnapshot(
                key="vfs.rebuild",
                state=RUNNING,
            )
        )
        tasks = _Tasks(operations)
        actions, api, events = self._actions(tasks)

        actions.run()

        self.assertEqual(tasks.runs, [])
        self.assertIn("Пересборка VFS уже выполняется.", api.logs)
        self.assertEqual(events.published, [])

    def test_scan_done_builds_summary_without_core_service_method(self):
        tasks = _Tasks()
        actions, api, events = self._actions(tasks)
        services = _Services()
        actions.services = services
        forest = _Forest()

        actions._on_scan_done(forest, None)

        self.assertEqual(services.calls, [(forest, Path("configs"), {})])
        self.assertEqual(api.ready, [forest])
        self.assertEqual(api.errors, [])
        self.assertTrue(
            any(
                "Корней: 3; файлов LTX: 12; XML: 2; "
                "в дереве system: 5; секций: 42" == status
                for status in api.statuses
            ),
            api.statuses,
        )
        self.assertTrue(
            any("Корней: 3; LTX: 12; XML: 2" in message for message in api.logs),
            api.logs,
        )

    def test_scan_cancelled_during_close_is_not_shown_as_error(self):
        actions, api, events = self._actions(_Tasks())
        api._closing = True

        actions._on_scan_done(None, RuntimeError("Операция отменена"))

        self.assertEqual(api.errors, [])
        self.assertNotIn(
            (Events.ERROR, "Операция отменена"),
            events.published,
        )
        self.assertIn(
            "Пересборка VFS прервана при закрытии",
            api.logs,
        )


if __name__ == "__main__":
    unittest.main()
