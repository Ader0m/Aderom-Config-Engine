# -*- coding: utf-8 -*-
"""Тесты публичного контракта модулей (SDK)."""

import unittest

from tests.support import SRC  # noqa: F401

from sdk.adapters import (
    CommandApiAdapter,
    CoreApiAdapter,
    EventApiAdapter,
    TaskApiAdapter,
    UiApiAdapter,
)
from sdk.contracts import ModuleBase, ModuleContext, ModuleManifest
from ui.bus import CommandBus, EventBus


class _Ui:
    def __init__(self):
        self.logs = []

    def log(self, message):
        self.logs.append(message)


class _Tasks:
    def run(self, *args, **kwargs):
        return None


class _Services:
    pass


class ModuleApiTest(unittest.TestCase):
    def test_manifest_defaults(self):
        manifest = ModuleManifest(name="test_module")

        self.assertEqual(manifest.name, "test_module")
        self.assertEqual(manifest.version, "0.0.0")
        self.assertEqual(manifest.sdk_min, "0.1.0")
        self.assertEqual(manifest.description, "")
        self.assertEqual(manifest.author, "")

    def test_module_base_lifecycle_methods_are_callable(self):
        module = ModuleBase()
        ui = _Ui()
        context = ModuleContext(
            "0.1.0",
            EventBus(),
            CommandBus(),
            ui,
            _Tasks(),
            _Services(),
            None,
        )

        module.init(context)
        module.register(context)
        module.activate(context)
        module.deactivate(context)

    def test_context_wraps_buses_and_is_not_global(self):
        first = ModuleContext(
            "0.1.0",
            EventBus(),
            CommandBus(),
            _Ui(),
            _Tasks(),
            _Services(),
            None,
        )
        second = ModuleContext(
            "0.1.0",
            EventBus(),
            CommandBus(),
            _Ui(),
            _Tasks(),
            _Services(),
            None,
        )

        self.assertIsInstance(first.events, EventApiAdapter)
        self.assertIsInstance(first.commands, CommandApiAdapter)
        self.assertIsInstance(first.ui, UiApiAdapter)
        self.assertIsInstance(first.tasks, TaskApiAdapter)
        self.assertIsInstance(first.services, CoreApiAdapter)
        self.assertIsNot(first.events, second.events)
        self.assertIsNot(first.commands, second.commands)
        self.assertIsNot(first.ui, second.ui)

    def test_context_log_delegates_to_ui(self):
        ui = _Ui()
        context = ModuleContext(
            "0.1.0",
            EventBus(),
            CommandBus(),
            ui,
            _Tasks(),
            _Services(),
            None,
        )

        context.log("hello")

        self.assertEqual(ui.logs, ["hello"])


if __name__ == "__main__":
    unittest.main()
