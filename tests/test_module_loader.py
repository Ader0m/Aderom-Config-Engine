# -*- coding: utf-8 -*-
"""Минимальные тесты загрузчика модулей.

Полный жизненный цикл модуля требует реального контекста платформы;
здесь фиксируется публичный контракт обнаружения и изоляции ошибок.
"""

import io
import unittest
from contextlib import redirect_stderr

from tests.support import SRC  # noqa: F401

from sdk.contracts import ModuleManifest
from ui.module_loader import ModuleLoader


class _Manifest:
    def __init__(self, name, requires=(), package=None):
        self.name = name
        self.version = "0.1.0"
        self.sdk_min = "0.1.0"
        self.requires = tuple(requires)
        self.package = package or name


class _Entry:
    def __init__(self, name, requires=(), package=None):
        self.manifest = _Manifest(name, requires, package)
        self.instance = None
        self.ctx = None
        self.package = package or name


class _Lifecycle:
    def __init__(self):
        self.init_called = False
        self.register_called = False
        self.deactivate_called = False

    def init(self, _ctx):
        self.init_called = True

    def register(self, _ctx):
        self.register_called = True
        raise RuntimeError("register failed")

    def deactivate(self, _ctx):
        self.deactivate_called = True


class ModuleLoaderTest(unittest.TestCase):
    def test_discover_passes_manifest_to_context_factory(self):
        manifests = []
        logs = []

        def make_context(manifest):
            manifests.append(manifest)
            return None

        loader = ModuleLoader(make_context)
        with redirect_stderr(io.StringIO()):
            loader.discover_and_load(log=logs.append)

        self.assertTrue(manifests)
        self.assertTrue(
            all(isinstance(item, ModuleManifest) for item in manifests)
        )
        self.assertTrue(
            all(item.sdk_min for item in manifests)
        )
        self.assertIn("base", {item.name for item in manifests})

    def test_dependencies_are_loaded_before_dependents(self):
        loader = ModuleLoader(lambda manifest: None)
        candidates = {
            "base": _Entry("base", package="base_module"),
            "z": _Entry("z", ("base",), package="z_module"),
            "a": _Entry("a", ("z",), package="a_module"),
        }
        ordered = loader._dependency_order(candidates, lambda _m: None)
        self.assertEqual(
            [entry.manifest.name for entry in ordered],
            ["base", "z", "a"],
        )

    def test_cycle_is_not_loaded(self):
        loader = ModuleLoader(lambda manifest: None)
        logs = []
        candidates = {
            "a": _Entry("a", ("b",)),
            "b": _Entry("b", ("a",)),
        }
        ordered = loader._dependency_order(candidates, logs.append)
        self.assertEqual(ordered, [])
        self.assertTrue(any("циклических" in line for line in logs))

    def test_register_failure_rolls_back_and_is_not_loaded(self):
        loader = ModuleLoader(lambda manifest: object())
        entry = _Entry("broken")
        entry.instance = _Lifecycle()
        logs = []
        loader._start_module(entry, {}, logs.append)

        self.assertEqual(loader.loaded, [])
        self.assertTrue(entry.instance.init_called)
        self.assertTrue(entry.instance.register_called)
        self.assertTrue(entry.instance.deactivate_called)
        self.assertTrue(any("init/register" in line for line in logs))

    def test_activate_and_deactivate_do_not_raise(self):
        loader = ModuleLoader(lambda manifest: None)
        with redirect_stderr(io.StringIO()):
            loader.discover_and_load(log=lambda message: None)
            loader.activate_all(log=lambda message: None)
            loader.deactivate_all(log=lambda message: None)


if __name__ == "__main__":
    unittest.main()
