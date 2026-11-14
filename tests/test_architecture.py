# -*- coding: utf-8 -*-
"""Проверка границ слоёв по импортам."""

import ast
import unittest
import warnings
from pathlib import Path

from tests.support import SRC

ROOT = Path(SRC)

KNOWN_FILES = {
    "app": ROOT / "app" / "projects" / "service.py",
    "core": ROOT / "core" / "facade.py",
    "ui": ROOT / "ui" / "module_loader.py",
    "sdk": ROOT / "sdk" / "contracts.py",
    "modules": ROOT / "modules" / "base_module" / "section_graph.py",
}


def _parse(path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        warnings.simplefilter("ignore", DeprecationWarning)
        return ast.parse(path.read_text(encoding="utf-8-sig"))


def _package_parts(path):
    rel = path.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    else:
        parts = parts[:-1]
    return parts


def _resolve_relative(package_parts, level, module):
    """Возвращает абсолютные части модуля для ``from ..x import y``."""
    if level <= 0:
        return list((module or "").split(".")) if module else []

    keep = len(package_parts) - (level - 1)
    if keep < 0:
        keep = 0
    parts = list(package_parts[:keep])
    if module:
        parts.extend(module.split("."))
    return parts


def _imports(path):
    """Возвращает корневые имена всех импортов, включая относительные."""
    tree = _parse(path)
    package_parts = _package_parts(path)
    roots = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])

        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = _resolve_relative(
                    package_parts,
                    node.level,
                    node.module,
                )
                if base:
                    roots.add(base[0])
                # ``from . import core`` импортирует подмодули пакета,
                # а не обязательно внешний core, но проверить корень
                # всё равно полезно на случай выхода за пределы пакета.
                if node.module is None:
                    for alias in node.names:
                        candidate = base + alias.name.split(".")
                        if candidate:
                            roots.add(candidate[0])
            elif node.module:
                roots.add(node.module.split(".")[0])

        elif isinstance(node, ast.Call):
            func = node.func
            is_dunder_import = (
                isinstance(func, ast.Name) and func.id == "__import__"
            )
            is_import_module = (
                isinstance(func, ast.Attribute)
                and func.attr == "import_module"
            )
            if not (is_dunder_import or is_import_module):
                continue
            if not node.args:
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                roots.add(arg.value.split(".")[0])

    return roots


def _files(package):
    package_root = ROOT / package
    if not package_root.is_dir():
        return []
    return [
        path
        for path in package_root.rglob("*.py")
        if "__pycache__" not in path.parts
    ]


class ArchitectureBoundaryTest(unittest.TestCase):
    def test_packages_are_discovered(self):
        for package, known in KNOWN_FILES.items():
            with self.subTest(package=package):
                files = _files(package)
                self.assertTrue(files, "не найдены .py файлы в %s" % package)
                self.assertIn(known, files, "не найден ожидаемый файл %s" % known)

    def test_modules_do_not_import_core_or_ui(self):
        for path in _files("modules"):
            roots = _imports(path)
            self.assertNotIn("core", roots, str(path))
            self.assertNotIn("ui", roots, str(path))

    def test_sdk_does_not_import_core_ui_or_modules(self):
        for path in _files("sdk"):
            roots = _imports(path)
            self.assertNotIn("core", roots, str(path))
            self.assertNotIn("ui", roots, str(path))
            self.assertNotIn("modules", roots, str(path))

    def test_app_does_not_import_ui_or_modules(self):
        for path in _files("app"):
            roots = _imports(path)
            self.assertNotIn("ui", roots, str(path))
            self.assertNotIn("modules", roots, str(path))

    def test_core_does_not_import_ui_sdk_or_modules(self):
        for path in _files("core"):
            roots = _imports(path)
            self.assertNotIn("ui", roots, str(path))
            self.assertNotIn("sdk", roots, str(path))
            self.assertNotIn("modules", roots, str(path))

    def test_sdk_loader_lives_outside_sdk(self):
        self.assertFalse((ROOT / "sdk" / "loader.py").exists())
        self.assertTrue((ROOT / "ui" / "module_loader.py").exists())

    def test_hint_contracts_are_qt_free(self):
        path = ROOT / "sdk" / "hints.py"
        self.assertTrue(path.exists())
        roots = _imports(path)
        self.assertNotIn("PyQt5", roots)
        self.assertNotIn("core", roots)
        self.assertNotIn("ui", roots)
        self.assertNotIn("modules", roots)

    def test_ltx_syntax_is_pure_core(self):
        path = ROOT / "core" / "patching" / "ltx" / "syntax.py"
        self.assertTrue(path.exists())
        roots = _imports(path)
        self.assertNotIn("PyQt5", roots)
        self.assertNotIn("ui", roots)
        self.assertNotIn("sdk", roots)
        self.assertNotIn("modules", roots)

    def test_relative_import_resolution_catches_escape(self):
        # Проверяем сам алгоритм на искусственном пути: из
        # modules/base_module/x.py уровень 3 выходит в корень src.
        package_parts = ["modules", "base_module"]
        self.assertEqual(
            _resolve_relative(package_parts, 3, "core"),
            ["core"],
        )
        self.assertEqual(
            _resolve_relative(package_parts, 2, "core"),
            ["modules", "core"],
        )


if __name__ == "__main__":
    unittest.main()
