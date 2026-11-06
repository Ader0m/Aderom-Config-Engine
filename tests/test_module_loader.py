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

    def test_activate_and_deactivate_do_not_raise(self):
        loader = ModuleLoader(lambda manifest: None)
        with redirect_stderr(io.StringIO()):
            loader.discover_and_load(log=lambda message: None)
            loader.activate_all(log=lambda message: None)
            loader.deactivate_all(log=lambda message: None)


if __name__ == "__main__":
    unittest.main()
