# -*- coding: utf-8 -*-
"""Conformance встроенного модуля-образца (Этап 7.7)."""

import ast
import importlib
import itertools
import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace

from PyQt5.QtWidgets import QWidget

from tests.qt_support import qapp
from tests.support import SRC  # noqa: F401

from sdk.contracts.events import PLATFORM_OWNER
from ui.bus import CommandBus, EventBus
from ui.editor_tabs import EditorTabs
from ui.module_loader import ModuleLoader, ModuleState
from ui.platform.sdk_bridge import build_module_context
from ui.platform_api import PlatformApi

_app = qapp()
_COUNTER = itertools.count(1)


class _TempModules:
    def __init__(self, case):
        self.temp = tempfile.TemporaryDirectory()
        case.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.name = "ace_conformance_%d" % next(_COUNTER)
        self.dir = self.root / self.name
        self.dir.mkdir()
        (self.dir / "__init__.py").write_text("", encoding="utf-8")
        sys.path.insert(0, str(self.root))
        case.addCleanup(self._cleanup)
        importlib.invalidate_caches()
        self.package = importlib.import_module(self.name)

    def _cleanup(self):
        for name in list(sys.modules):
            if name == self.name or name.startswith(self.name + "."):
                sys.modules.pop(name, None)
        try:
            sys.path.remove(str(self.root))
        except ValueError:
            pass

    def add(self, module_id, manifest, source):
        module_dir = self.dir / module_id
        module_dir.mkdir()
        (module_dir / "__init__.py").write_text("", encoding="utf-8")
        (module_dir / "module.py").write_text(
            textwrap.dedent(source),
            encoding="utf-8",
        )
        (module_dir / "module.json").write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
        )


def _manifest(module_id, provides=(), optional_needs=()):
    return {
        "manifest_version": 1,
        "id": module_id,
        "name": module_id,
        "version": "0.1.0",
        "entrypoint": "module:MODULE",
        "sdk": {"min": "0.12.0"},
        "requires": [],
        "optional_requires": [],
        "needs": [],
        "optional_needs": list(optional_needs),
        "provides": list(provides),
        "trust": "trusted",
        "enabled_by_default": True,
    }


SAMPLE_SOURCE = """
from sdk import ModuleBase
from sdk.ui import WindowSpec

from PyQt5.QtWidgets import QWidget


class SampleModule(ModuleBase):
    def init(self, ctx):
        super().init(ctx)
        self.window = None
        self.optional = ctx.capabilities.optional(
            "absent.cap", "1.0.0"
        )

    def register(self, ctx):
        ctx.capabilities.provide("sample.cap", "1.0.0", object())
        ctx.commands.register("sample.run", lambda: None)
        self.menu = ctx.ui.register_menu(
            "Инструменты", "Sample", lambda: None
        )

    def activate(self, ctx):
        self.window = QWidget()
        ctx.ui.open_window(
            WindowSpec(
                key="sample.window",
                title="Sample",
                kind="floating",
            ),
            self.window,
        )

    def deactivate(self, ctx):
        if self.window is not None:
            self.window.close()


MODULE = SampleModule()
"""


class ModuleConformanceTest(unittest.TestCase):
    def _make_context(self, root):
        events = EventBus()
        commands = CommandBus()
        paths = SimpleNamespace(
            projects_dir=root / "projects",
            app_dir=root,
        )
        api = PlatformApi(events, commands, None, object(), paths)

        def make_context(manifest):
            return build_module_context(
                manifest,
                sdk_version="0.12.0",
                events=events,
                commands=commands,
                ui=api,
                tasks=None,
                services=object(),
                paths=paths,
            )

        return api, commands, make_context

    def test_sample_module_conformance(self):
        package = _TempModules(self)
        package.add(
            "sample",
            _manifest(
                "sample",
                provides=[
                    {"name": "sample.cap", "version": "1.0.0"}
                ],
                optional_needs=[
                    {"name": "absent.cap", "min_version": "1.0.0"}
                ],
            ),
            SAMPLE_SOURCE,
        )
        api, commands, make_context = self._make_context(package.root)
        loader = ModuleLoader(make_context, package=package.package)

        loader.discover_and_load(log=lambda message: None)
        loader.activate_all(log=lambda message: None)

        record = loader.records["sample"]
        self.assertEqual(record.state, ModuleState.ACTIVE)
        self.assertIsNone(record.instance.optional)
        self.assertTrue(commands.has("sample.run"))
        self.assertTrue(record.instance.window.isVisible())
        self.assertTrue(api.menu_registrations)
        self.assertTrue(
            loader.capability_registry.has("sample.cap")
        )
        window = record.instance.window

        loader.unload("sample", log=lambda message: None)

        self.assertEqual(record.state, ModuleState.UNLOADED)
        self.assertFalse(commands.has("sample.run"))
        self.assertEqual(api.menu_registrations, [])
        self.assertFalse(
            loader.capability_registry.has("sample.cap")
        )
        self.assertFalse(window.isVisible())
        self.assertNotIn(package.name + ".sample", sys.modules)

    def test_sample_imports_only_sdk_and_pyqt(self):
        tree = ast.parse(SAMPLE_SOURCE)
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])

        self.assertTrue(roots <= {"sdk", "PyQt5"})
        self.assertIn("sdk", roots)

    def test_register_error_is_rolled_back(self):
        package = _TempModules(self)
        package.add(
            "broken",
            _manifest("broken"),
            """
            from sdk import ModuleBase

            class Broken(ModuleBase):
                def register(self, ctx):
                    ctx.commands.register("broken.run", lambda: None)
                    raise RuntimeError("register failed")

            MODULE = Broken()
            """,
        )
        api, commands, make_context = self._make_context(package.root)
        loader = ModuleLoader(make_context, package=package.package)

        loader.discover_and_load(log=lambda message: None)

        record = loader.records["broken"]
        self.assertEqual(record.state, ModuleState.FAILED)
        self.assertEqual(record.phase, "register")
        self.assertFalse(commands.has("broken.run"))
        self.assertFalse(api.menu_registrations)

    def test_foreign_tab_is_not_closed(self):
        tabs = EditorTabs()
        self.addCleanup(tabs.deleteLater)
        widget = QWidget()
        tabs.open_tab(
            "foreign",
            "Foreign",
            widget,
            owner=PLATFORM_OWNER,
        )

        self.assertFalse(tabs.close_tab("foreign", owner="sample"))
        self.assertTrue(tabs.get_tab("foreign") is widget)
        self.assertTrue(
            tabs.close_tab("foreign", owner=PLATFORM_OWNER)
        )


if __name__ == "__main__":
    unittest.main()
