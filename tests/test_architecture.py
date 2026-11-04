# -*- coding: utf-8 -*-
"""Проверка границ слоёв по импортам."""

import ast
import unittest
import warnings
from pathlib import Path

from tests.support import SRC  # noqa: F401


ROOT = Path(__file__).resolve().parents[1] / "src"


def _imported_roots(path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    roots = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            if node.module:
                roots.append(node.module.split(".")[0])

    return set(roots)


def _files(package):
    return [
        path
        for path in (ROOT / package).rglob("*.py")
        if "__pycache__" not in path.parts
    ]


class ArchitectureBoundaryTest(unittest.TestCase):
    def test_modules_do_not_import_core_or_ui(self):
        for path in _files("modules"):
            roots = _imported_roots(path)
            self.assertNotIn("core", roots, str(path))
            self.assertNotIn("ui", roots, str(path))

    def test_sdk_does_not_import_core_ui_or_modules(self):
        for path in _files("sdk"):
            roots = _imported_roots(path)
            self.assertNotIn("core", roots, str(path))
            self.assertNotIn("ui", roots, str(path))
            self.assertNotIn("modules", roots, str(path))

    def test_core_does_not_import_ui_sdk_or_modules(self):
        for path in _files("core"):
            roots = _imported_roots(path)
            self.assertNotIn("ui", roots, str(path))
            self.assertNotIn("sdk", roots, str(path))
            self.assertNotIn("modules", roots, str(path))

    def test_sdk_loader_lives_outside_sdk(self):
        self.assertFalse((ROOT / "sdk" / "loader.py").exists())
        self.assertTrue((ROOT / "ui" / "module_loader.py").exists())


if __name__ == "__main__":
    unittest.main()
