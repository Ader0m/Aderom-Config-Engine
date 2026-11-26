# -*- coding: utf-8 -*-
"""Схема ``module.json`` и отсутствие class-manifest."""

import json
import tempfile
import unittest
from pathlib import Path

from tests.support import SRC

from ui.platform.module_manifest import (
    ModuleManifestError,
    read_module_manifest,
)


def _valid(module_id="demo", **overrides):
    data = {
        "manifest_version": 1,
        "id": module_id,
        "name": "Демо",
        "version": "0.1.0",
        "description": "Тестовый модуль.",
        "author": "ACE",
        "entrypoint": "module:MODULE",
        "sdk": {"min": "0.11.0", "max": "1.0.0"},
        "requires": [{"id": "base", "min_version": "0.4.0"}],
        "optional_requires": [],
        "needs": [{"name": "section.graph", "min_version": "1.0.0"}],
        "optional_needs": [],
        "provides": [{"name": "demo.cap", "version": "1.0.0"}],
        "trust": "trusted",
        "enabled_by_default": True,
        "platforms": ["windows"],
    }
    data.update(overrides)
    return data


class _Workspace:
    def __init__(self, case):
        self.temp = tempfile.TemporaryDirectory()
        case.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, module_id, data):
        module_dir = self.root / module_id
        module_dir.mkdir()
        (module_dir / "module.json").write_text(
            json.dumps(data, ensure_ascii=False),
            encoding="utf-8",
        )
        return module_dir


class ModuleManifestTest(unittest.TestCase):
    def test_valid_manifest_is_parsed(self):
        workspace = _Workspace(self)
        module_dir = workspace.write("demo", _valid())

        manifest = read_module_manifest(module_dir)

        self.assertEqual(manifest.id, "demo")
        self.assertEqual(manifest.name, "Демо")
        self.assertEqual(manifest.version, "0.1.0")
        self.assertEqual(manifest.manifest_version, 1)
        self.assertEqual(manifest.entrypoint, "module:MODULE")
        self.assertEqual(manifest.sdk_min, "0.11.0")
        self.assertEqual(manifest.sdk_max, "1.0.0")
        self.assertEqual(manifest.trust, "trusted")
        self.assertTrue(manifest.enabled_by_default)
        self.assertEqual(manifest.platforms, ("windows",))
        self.assertEqual(manifest.requires[0].id, "base")
        self.assertEqual(manifest.requires[0].min_version, "0.4.0")
        self.assertEqual(manifest.needs[0].name, "section.graph")
        self.assertEqual(manifest.needs[0].min_version, "1.0.0")
        self.assertEqual(manifest.provides[0].name, "demo.cap")
        self.assertEqual(manifest.provides[0].version, "1.0.0")

    def test_platforms_default_is_windows(self):
        workspace = _Workspace(self)
        data = _valid()
        del data["platforms"]
        module_dir = workspace.write("demo", data)

        manifest = read_module_manifest(module_dir)

        self.assertEqual(manifest.platforms, ("windows",))

    def test_unknown_fields_are_ignored_with_warning(self):
        workspace = _Workspace(self)
        data = _valid(extra_field=True)
        data["sdk"]["extra"] = "ignored"
        module_dir = workspace.write("demo", data)
        warnings = []

        manifest = read_module_manifest(module_dir, warn=warnings.append)

        self.assertEqual(manifest.id, "demo")
        self.assertTrue(
            any("extra_field" in message for message in warnings)
        )

    def test_missing_manifest(self):
        workspace = _Workspace(self)
        module_dir = workspace.root / "demo"
        module_dir.mkdir()

        with self.assertRaises(ModuleManifestError) as ctx:
            read_module_manifest(module_dir)

        self.assertEqual(ctx.exception.kind, "manifest_missing")

    def test_invalid_json(self):
        workspace = _Workspace(self)
        module_dir = workspace.root / "demo"
        module_dir.mkdir()
        (module_dir / "module.json").write_text("{", encoding="utf-8")

        with self.assertRaises(ModuleManifestError) as ctx:
            read_module_manifest(module_dir)

        self.assertEqual(ctx.exception.kind, "manifest_invalid")

    def test_unsupported_manifest_version_does_not_fall_back(self):
        workspace = _Workspace(self)
        module_dir = workspace.write("demo", _valid(manifest_version=2))

        with self.assertRaises(ModuleManifestError) as ctx:
            read_module_manifest(module_dir)

        self.assertEqual(
            ctx.exception.kind,
            "unsupported_manifest_version",
        )

    def test_id_must_match_directory(self):
        workspace = _Workspace(self)
        module_dir = workspace.write("demo", _valid(id="other"))

        with self.assertRaises(ModuleManifestError) as ctx:
            read_module_manifest(module_dir)

        self.assertEqual(ctx.exception.kind, "id_mismatch")

    def test_id_syntax_is_validated(self):
        workspace = _Workspace(self)
        module_dir = workspace.write("Demo", _valid(id="Demo"))

        with self.assertRaises(ModuleManifestError) as ctx:
            read_module_manifest(module_dir)

        self.assertEqual(ctx.exception.kind, "manifest_invalid")

    def test_string_dependency_shortcut_is_rejected(self):
        workspace = _Workspace(self)
        module_dir = workspace.write(
            "demo",
            _valid(requires=["base"]),
        )

        with self.assertRaises(ModuleManifestError) as ctx:
            read_module_manifest(module_dir)

        self.assertEqual(ctx.exception.kind, "manifest_invalid")

    def test_capability_requirement_requires_min_version(self):
        workspace = _Workspace(self)
        module_dir = workspace.write(
            "demo",
            _valid(needs=[{"name": "section.graph"}]),
        )

        with self.assertRaises(ModuleManifestError) as ctx:
            read_module_manifest(module_dir)

        self.assertEqual(ctx.exception.kind, "manifest_invalid")

    def test_capability_declaration_requires_version(self):
        workspace = _Workspace(self)
        module_dir = workspace.write(
            "demo",
            _valid(provides=[{"name": "demo.cap"}]),
        )

        with self.assertRaises(ModuleManifestError) as ctx:
            read_module_manifest(module_dir)

        self.assertEqual(ctx.exception.kind, "manifest_invalid")

    def test_unknown_trust_is_rejected(self):
        workspace = _Workspace(self)
        module_dir = workspace.write(
            "demo",
            _valid(trust="unknown"),
        )

        with self.assertRaises(ModuleManifestError) as ctx:
            read_module_manifest(module_dir)

        self.assertEqual(ctx.exception.kind, "manifest_invalid")

    def test_enabled_by_default_must_be_boolean(self):
        workspace = _Workspace(self)
        module_dir = workspace.write(
            "demo",
            _valid(enabled_by_default=1),
        )

        with self.assertRaises(ModuleManifestError) as ctx:
            read_module_manifest(module_dir)

        self.assertEqual(ctx.exception.kind, "manifest_invalid")

    def test_entrypoint_shape_is_validated(self):
        workspace = _Workspace(self)
        module_dir = workspace.write(
            "demo",
            _valid(entrypoint="demo"),
        )

        with self.assertRaises(ModuleManifestError) as ctx:
            read_module_manifest(module_dir)

        self.assertEqual(ctx.exception.kind, "manifest_invalid")

    def test_bad_semver_is_rejected(self):
        workspace = _Workspace(self)
        module_dir = workspace.write(
            "demo",
            _valid(version="1.0"),
        )

        with self.assertRaises(ModuleManifestError) as ctx:
            read_module_manifest(module_dir)

        self.assertEqual(ctx.exception.kind, "manifest_invalid")


class BuiltinModuleManifestTest(unittest.TestCase):
    def test_every_builtin_module_has_module_json(self):
        modules_root = SRC / "modules"
        for module_dir in sorted(modules_root.iterdir()):
            if not module_dir.is_dir() or module_dir.name.startswith("__"):
                continue
            self.assertTrue(
                (module_dir / "module.json").is_file(),
                "%s: нет module.json" % module_dir.name,
            )
            manifest = read_module_manifest(module_dir)
            self.assertEqual(manifest.id, module_dir.name)

    def test_builtin_capability_map(self):
        manifests = {
            module_dir.name: read_module_manifest(module_dir)
            for module_dir in (SRC / "modules").iterdir()
            if module_dir.is_dir() and not module_dir.name.startswith("__")
        }

        self.assertEqual(
            {item.name for item in manifests["base"].provides},
            {
                "document.viewer.ltx",
                "document.viewer.xml",
                "section.graph",
            },
        )
        self.assertEqual(
            {item.name for item in manifests["item_browser"].provides},
            {"items.catalog", "item.section.dragdrop"},
        )
        self.assertEqual(
            {item.name for item in manifests["ogg_editor"].provides},
            {"audio.ogg.editor"},
        )
        self.assertEqual(
            {item.name for item in manifests["ogg_editor"].needs},
            {"section.graph"},
        )
        self.assertEqual(
            {
                item.name
                for item in manifests["ogg_editor"].optional_needs
            },
            {"item.section.dragdrop"},
        )
        self.assertEqual(manifests["item_browser"].requires, ())
        self.assertEqual(manifests["item_browser"].optional_requires, ())

    def test_base_does_not_own_item_viewer(self):
        source = (SRC / "modules" / "base" / "module.py").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("item_viewer", source)
        self.assertNotIn("ItemViewerFeature", source)
        self.assertNotIn("CleanRunFeature", source)
        self.assertTrue(
            (SRC / "ui" / "platform" / "workspace_actions.py").is_file()
        )
        self.assertTrue(
            (SRC / "ui" / "platform" / "common_actions.py").is_file()
        )

    def test_builtin_modules_are_in_platform_trust_policy(self):
        from ui.platform.module_trust import SHIPPED_MODULE_IDS

        modules_root = SRC / "modules"
        for module_dir in sorted(modules_root.iterdir()):
            if not module_dir.is_dir() or module_dir.name.startswith("__"):
                continue
            self.assertIn(module_dir.name, SHIPPED_MODULE_IDS)

    def test_builtin_modules_have_no_class_manifest(self):
        from modules.base.module import BaseModule
        from modules.item_browser.module import ItemBrowserModule
        from modules.ogg_editor.module import OggEditorModule

        for module_class in (
            BaseModule,
            ItemBrowserModule,
            OggEditorModule,
        ):
            self.assertNotIn("manifest", vars(module_class))


if __name__ == "__main__":
    unittest.main()
