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

    def log(self, message):
        self.logs.append(str(message))

    def set_status(self, message):
        self.statuses.append(str(message))

    def project_overlay(self):
        return None

    def ask_directory(self, _path=""):
        return ""

    def show_error(self, _message):
        pass


class _Tasks:
    def __init__(self, operations=None):
        self.operations = operations or OperationRegistry()
        self.runs = []

    def run(self, fn, **kwargs):
        self.runs.append((fn, kwargs))
        return object()


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


if __name__ == "__main__":
    unittest.main()
