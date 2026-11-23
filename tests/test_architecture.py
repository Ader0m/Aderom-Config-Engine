# -*- coding: utf-8 -*-
"""Проверка границ слоёв по импортам.

Правила матрицы импортов — docs/Целевая архитектура приложения.md, §3.3.
Текущие legacy-исключения зафиксированы в tests/architecture_allowlist.py
и docs/Миграционные исключения.md. Новые исключения добавляет только
оператор.
"""

import ast
import re
import tempfile
import threading
import unittest
import warnings
from pathlib import Path

from tests.architecture_allowlist import (
    APP_SDK_IMPORT_ALLOWLIST,
    CORE_PUBLIC_NAMES,
    MODULES_PACKAGE_IMPORTERS,
    QT_WIDGETS_COMPOSITION_ROOTS,
    SDK_PUBLIC_NAMES,
    UI_CORE_IMPORT_ALLOWLIST,
)
from tests.support import SRC

ROOT = Path(SRC)

KNOWN_FILES = {
    "app": ROOT / "app" / "projects" / "service.py",
    "core": ROOT / "core" / "facade.py",
    "ui": ROOT / "ui" / "module_loader.py",
    "sdk": ROOT / "sdk" / "kernel" / "context.py",
    "modules": ROOT / "modules" / "base_module" / "section_graph.py",
}


def _parse(path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        warnings.simplefilter("ignore", DeprecationWarning)
        return ast.parse(path.read_text(encoding="utf-8-sig"))


def _package_parts(path, root=ROOT):
    rel = path.relative_to(root).with_suffix("")
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


def _import_names(path, root=ROOT):
    """Возвращает полные имена всех импортов, включая относительные."""
    tree = _parse(path)
    package_parts = _package_parts(path, root)
    names = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)

        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = _resolve_relative(
                    package_parts,
                    node.level,
                    node.module,
                )
                if base:
                    names.add(".".join(base))
                # ``from . import core`` импортирует подмодули пакета,
                # а не обязательно внешний core. Проверяем оба варианта.
                if node.module is None:
                    for alias in node.names:
                        names.add(".".join(base + [alias.name]))
                else:
                    for alias in node.names:
                        names.add(".".join(base + [alias.name]))
            elif node.module:
                names.add(node.module)
                for alias in node.names:
                    names.add(node.module + "." + alias.name)

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
                names.add(arg.value)

    return {name for name in names if name}


def _imports(path, root=ROOT):
    """Возвращает корневые имена всех импортов, включая относительные."""
    return {
        name.split(".")[0]
        for name in _import_names(path, root)
    }


def _module_targets(path, root=ROOT):
    """Возвращает только целевые модули импорта, без импортируемых имён."""
    tree = _parse(path)
    package_parts = _package_parts(path, root)
    targets = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.add(alias.name)

        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = _resolve_relative(
                    package_parts,
                    node.level,
                    node.module,
                )
                if base:
                    targets.add(".".join(base))
            elif node.module:
                targets.add(node.module)

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
                targets.add(arg.value)

    return targets


def _files(package):
    package_root = ROOT / package
    if not package_root.is_dir():
        return []
    return [
        path
        for path in package_root.rglob("*.py")
        if "__pycache__" not in path.parts
    ]


def _all_src_files():
    return [
        path
        for path in ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    ]


def _rel(path):
    return path.relative_to(ROOT).as_posix()


class ArchitectureBoundaryTest(unittest.TestCase):
    def test_packages_are_discovered(self):
        for package, known in KNOWN_FILES.items():
            with self.subTest(package=package):
                files = _files(package)
                self.assertTrue(files, "не найдены .py файлы в %s" % package)
                self.assertIn(known, files, "не найден ожидаемый файл %s" % known)

    def test_modules_do_not_import_core_app_platform_or_ui(self):
        for path in _files("modules"):
            roots = _imports(path)
            for forbidden in ("core", "app", "platform", "ui"):
                self.assertNotIn(forbidden, roots, str(path))

    def test_modules_do_not_import_other_modules(self):
        for path in _files("modules"):
            own = path.relative_to(ROOT / "modules").parts[0]
            for name in _import_names(path):
                parts = name.split(".")
                if len(parts) > 1 and parts[0] == "modules":
                    self.assertEqual(
                        parts[1],
                        own,
                        "модуль %s импортирует чужой пакет: %s"
                        % (path, name),
                    )

    def test_sdk_does_not_import_internal_layers(self):
        for path in _files("sdk"):
            roots = _imports(path)
            for forbidden in ("core", "app", "platform", "ui", "modules"):
                self.assertNotIn(forbidden, roots, str(path))

    def test_app_does_not_import_ui_platform_or_modules(self):
        for path in _files("app"):
            roots = _imports(path)
            for forbidden in ("ui", "platform", "modules"):
                self.assertNotIn(forbidden, roots, str(path))

    def test_app_has_no_qt(self):
        for path in _files("app"):
            roots = _imports(path)
            self.assertNotIn("PyQt5", roots, str(path))

    def test_app_imports_sdk_only_from_allowlist(self):
        for path in _files("app"):
            for target in _module_targets(path):
                if target != "sdk" and not target.startswith("sdk."):
                    continue
                self.assertIn(
                    target,
                    APP_SDK_IMPORT_ALLOWLIST,
                    "app импортирует sdk вне allowlist: %s -> %s"
                    % (_rel(path), target),
                )

    def test_core_and_sdk_have_no_qt(self):
        for layer in ("core", "sdk"):
            for path in _files(layer):
                roots = _imports(path)
                self.assertNotIn(
                    "PyQt5",
                    roots,
                    "Qt в слое %s: %s" % (layer, path),
                )

    def test_core_does_not_import_other_layers(self):
        for path in _files("core"):
            roots = _imports(path)
            for forbidden in ("sdk", "app", "platform", "ui", "modules"):
                self.assertNotIn(forbidden, roots, str(path))

    def test_ui_core_imports_are_allowlisted(self):
        found = set()
        for path in _files("ui"):
            if "core" not in _imports(path):
                continue
            rel = _rel(path)
            found.add(rel)
            self.assertIn(
                rel,
                UI_CORE_IMPORT_ALLOWLIST,
                "ui импортирует core вне legacy-исключения: %s" % rel,
            )

        # Allowlist не должен содержать мёртвых исключений.
        for rel in UI_CORE_IMPORT_ALLOWLIST:
            path = ROOT / rel
            self.assertTrue(path.is_file(), rel)
            self.assertIn("core", _imports(path), rel)

    def test_ui_does_not_import_modules(self):
        for path in _files("ui"):
            if _rel(path) in MODULES_PACKAGE_IMPORTERS:
                continue
            roots = _imports(path)
            self.assertNotIn("modules", roots, str(path))

    def test_modules_package_is_imported_only_by_loader(self):
        for path in _all_src_files():
            rel = _rel(path)
            if rel.startswith("modules/"):
                continue
            if rel in MODULES_PACKAGE_IMPORTERS:
                continue
            for name in _import_names(path):
                if name == "modules" or name.startswith("modules."):
                    self.fail(
                        "импорт пакета modules вне ModuleLoader: %s -> %s"
                        % (rel, name)
                    )

        for rel in MODULES_PACKAGE_IMPORTERS:
            path = ROOT / rel
            self.assertTrue(path.is_file(), rel)
            self.assertIn("modules", _imports(path), rel)

    def test_qt_widgets_are_limited_to_ui_and_modules(self):
        for path in _all_src_files():
            rel = _rel(path)
            if (
                rel.startswith("ui/")
                or rel.startswith("modules/")
                or rel in QT_WIDGETS_COMPOSITION_ROOTS
            ):
                continue
            for name in _import_names(path):
                if name == "PyQt5.QtWidgets" or name.startswith(
                    "PyQt5.QtWidgets."
                ):
                    self.fail(
                        "PyQt5.QtWidgets вне ui/modules: %s -> %s"
                        % (rel, name)
                    )

    def test_public_api_allowlist(self):
        import core
        import sdk

        self.assertEqual(set(sdk.__all__), set(SDK_PUBLIC_NAMES))
        self.assertEqual(set(core.__all__), set(CORE_PUBLIC_NAMES))
        self.assertEqual(len(sdk.__all__), len(set(sdk.__all__)))
        self.assertEqual(len(core.__all__), len(set(core.__all__)))

    def test_sdk_root_has_no_domain_items(self):
        import sdk

        for name in (
            "AssetDraftInfo",
            "FileView",
            "ItemCardRequest",
            "ItemCatalog",
            "ItemEntry",
            "ItemField",
            "ItemIcon",
            "ItemSection",
            "ItemSectionData",
            "LocalizationString",
            "PatchTargetInfo",
            "ProjectAssetType",
            "ProjectSetting",
            "ProjectSettingChoice",
            "ResolvedDocumentChange",
            "ResolvedDraftInfo",
            "ResolvedLineEdit",
            "SavedDraft",
            "TextureRegion",
            "XmlResolveResult",
        ):
            self.assertNotIn(name, sdk.__all__, name)
            self.assertFalse(hasattr(sdk, name), name)

    def test_old_sdk_modules_are_removed(self):
        for rel in (
            "sdk/adapters.py",
            "sdk/api.py",
            "sdk/constants.py",
            "sdk/contracts.py",
            "sdk/dragdrop.py",
            "sdk/engine_text.py",
            "sdk/events.py",
            "sdk/forest.py",
            "sdk/hints.py",
            "sdk/paths.py",
            "sdk/registrations.py",
            "sdk/version.py",
        ):
            self.assertFalse((ROOT / rel).exists(), rel)

    def test_sdk_bridge_does_not_lookup_methods_by_name(self):
        bridge = ROOT / "ui" / "platform" / "sdk_bridge"
        for path in bridge.glob("*.py"):
            tree = _parse(path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if not (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "getattr"
                ):
                    continue
                if len(node.args) < 2:
                    continue
                name = node.args[1]
                if not isinstance(name, ast.Constant):
                    continue
                if not isinstance(name.value, str):
                    continue
                target = ast.unparse(node.args[0])
                if target in ("self._platform", "self._service"):
                    self.fail(
                        "bridge ищет метод по имени: %s -> %s"
                        % (_rel(path), name.value)
                    )

    def test_app_imports_core_only_through_ports(self):
        for path in _files("app"):
            for name in _import_names(path):
                if name == "core":
                    self.fail(
                        "app импортирует core напрямую: %s" % _rel(path)
                    )
                if name.startswith("core.") and not name.startswith("core.api"):
                    self.fail(
                        "app импортирует внутренности core: %s -> %s"
                        % (_rel(path), name)
                    )

    def test_sdk_loader_lives_outside_sdk(self):
        self.assertFalse((ROOT / "sdk" / "loader.py").exists())
        self.assertTrue((ROOT / "ui" / "module_loader.py").exists())

    def test_hint_contracts_are_qt_free(self):
        path = ROOT / "sdk" / "ui" / "hints.py"
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

    def test_import_matrix_detects_artificial_violation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "src"
            path = root / "modules" / "base_module" / "broken.py"
            path.parent.mkdir(parents=True)
            path.write_text("from core import demo\n", encoding="utf-8")

            self.assertIn("core", _imports(path, root=root))

            clean = root / "modules" / "base_module" / "clean.py"
            clean.write_text("from . import helper\n", encoding="utf-8")
            self.assertNotIn("core", _imports(clean, root=root))

    def test_dispatch_is_rejected_from_background_thread(self):
        from tests.qt_support import qapp
        from ui.bus import EventBus

        app = qapp()
        self.assertIsNotNone(app)
        bus = EventBus()
        calls = []
        bus.subscribe(
            "action",
            lambda payload: calls.append(payload) or True,
        )

        result = {}

        def run():
            result["value"] = bus.dispatch("action", 1)

        thread = threading.Thread(target=run)
        thread.start()
        thread.join(5)

        self.assertFalse(thread.is_alive())
        self.assertFalse(result.get("value"))
        self.assertEqual(calls, [])


class DocumentMutationBoundaryTest(unittest.TestCase):
    """UI меняет документы только через ``ResolvedDocumentSession.apply``."""

    MUTATORS = (
        "apply_line_edit",
        "apply_field_edit",
        "rename_field",
        "delete_field",
        "insert_field",
        "replace_text",
        "reset",
        "undo",
        "redo",
    )

    def test_ui_does_not_call_document_mutators_directly(self):
        pattern = re.compile(
            r"\b(?:document|self\._document)\.(?:%s)\s*\("
            % "|".join(self.MUTATORS)
        )
        offenders = []
        for path in sorted((ROOT / "ui").rglob("*.py")):
            text = path.read_text(encoding="utf-8-sig")
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                offenders.append("%s:%d" % (_rel(path), line))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
