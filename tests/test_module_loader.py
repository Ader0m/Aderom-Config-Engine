# -*- coding: utf-8 -*-
"""Тесты загрузчика модулей: манифест, обнаружение и изоляция ошибок.

Полный жизненный цикл модуля требует реального контекста платформы;
здесь фиксируется публичный контракт обнаружения, чтения ``module.json``
до импорта кода и изоляции ошибок.
"""

import importlib
import io
import itertools
import json
import os
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr
from types import SimpleNamespace
from pathlib import Path

from tests.support import SRC  # noqa: F401

from sdk.kernel import (
    CapabilityDeclaration,
    CapabilityRequirement,
    ModuleManifest,
    ModuleRequirement,
)
from ui.module_loader import ModuleLoader, ModuleRecord, ModuleState
from ui.platform.module_config import ModuleConfigStore
from ui.platform.module_trust import (
    REQUIRED_MODULE_IDS,
    SHIPPED_MODULE_IDS,
)


class _Manifest:
    """Манифест-заглушка для проверки графа без реального пакета."""

    def __init__(
        self,
        module_id,
        requires=(),
        package=None,
        provides=(),
        needs=(),
        optional_needs=(),
        optional_requires=(),
    ):
        self.id = module_id
        self.name = module_id
        self.version = "0.1.0"
        self.sdk_min = "0.1.0"
        self.sdk_max = ""
        self.requires = tuple(requires)
        self.optional_requires = tuple(optional_requires)
        self.provides = tuple(provides)
        self.needs = tuple(needs)
        self.optional_needs = tuple(optional_needs)
        self.trust = "trusted"
        self.enabled_by_default = True
        self.platforms = ("windows",)
        self.package = package or module_id


class _Entry:
    def __init__(self, name, requires=(), package=None, provides=(), needs=()):
        self.manifest = _Manifest(
            name,
            requires,
            package,
            provides=provides,
            needs=needs,
        )
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


_COUNTER = itertools.count(1)


def _manifest(module_id, **overrides):
    data = {
        "manifest_version": 1,
        "id": module_id,
        "name": module_id,
        "version": "0.1.0",
        "entrypoint": "module:MODULE",
        "sdk": {"min": "0.11.0"},
        "trust": "trusted",
        "enabled_by_default": True,
    }
    data.update(overrides)
    return data


class _TempModules:
    """Временный пакет модулей для проверки discovery без записи в src."""

    def __init__(self, case, name=None):
        self.case = case
        self.temp = tempfile.TemporaryDirectory()
        case.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.name = name or "ace_modules_%d_%d" % (
            os.getpid(),
            next(_COUNTER),
        )
        self.package_dir = self.root / self.name
        self.package_dir.mkdir()
        (self.package_dir / "__init__.py").write_text(
            "",
            encoding="utf-8",
        )
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

    def add(
        self,
        module_id,
        manifest=None,
        source=None,
        init_source="",
    ):
        module_dir = self.package_dir / module_id
        module_dir.mkdir()
        (module_dir / "__init__.py").write_text(
            init_source,
            encoding="utf-8",
        )
        if source is None:
            source = (
                "from sdk import ModuleBase\n"
                "class Demo(ModuleBase):\n"
                "    pass\n"
                "MODULE = Demo()\n"
            )
        (module_dir / "module.py").write_text(
            textwrap.dedent(source),
            encoding="utf-8",
        )
        if manifest is not None:
            (module_dir / "module.json").write_text(
                json.dumps(manifest, ensure_ascii=False),
                encoding="utf-8",
            )
        return module_dir


class ManifestDiscoveryTest(unittest.TestCase):
    def test_valid_module_is_discovered_without_class_manifest(self):
        package = _TempModules(self)
        package.add("demo", _manifest("demo"))

        loader = ModuleLoader(lambda manifest: None, package=package.package)
        candidates = loader.discover(log=lambda message: None)

        self.assertEqual(set(candidates), {"demo"})
        manifest = candidates["demo"].manifest
        self.assertIsInstance(manifest, ModuleManifest)
        self.assertEqual(manifest.id, "demo")
        self.assertEqual(manifest.name, "demo")
        self.assertEqual(manifest.entrypoint, "module:MODULE")

    def test_missing_manifest_does_not_import_code(self):
        package = _TempModules(self)
        module_dir = package.add(
            "demo",
            manifest=None,
            source="raise RuntimeError('imported')\n",
        )
        loader = ModuleLoader(lambda manifest: None, package=package.package)
        logs = []

        candidates = loader.discover(log=logs.append)

        self.assertEqual(candidates, {})
        self.assertEqual(loader.skipped["demo"][0].kind, "manifest_missing")
        self.assertTrue(
            any("манифест отклонён" in message for message in logs)
        )
        self.assertFalse((module_dir / "__pycache__").exists())

    def test_invalid_manifest_does_not_import_code(self):
        package = _TempModules(self)
        marker = package.root / "imported.marker"
        package.add(
            "demo",
            {"manifest_version": 1},
            source=(
                "from pathlib import Path\n"
                "Path(%r).write_text('yes', encoding='utf-8')\n"
                "from sdk import ModuleBase\n"
                "class Demo(ModuleBase):\n"
                "    pass\n"
                "MODULE = Demo()\n" % str(marker)
            ),
        )
        loader = ModuleLoader(lambda manifest: None, package=package.package)

        candidates = loader.discover(log=lambda message: None)

        self.assertEqual(candidates, {})
        self.assertEqual(loader.skipped["demo"][0].kind, "manifest_invalid")
        self.assertFalse(marker.exists())

    def test_unsupported_manifest_version_is_skipped(self):
        package = _TempModules(self)
        package.add("demo", _manifest("demo", manifest_version=2))
        loader = ModuleLoader(lambda manifest: None, package=package.package)

        candidates = loader.discover(log=lambda message: None)

        self.assertEqual(candidates, {})
        self.assertEqual(
            loader.skipped["demo"][0].kind,
            "unsupported_manifest_version",
        )

    def test_id_must_match_package_directory(self):
        package = _TempModules(self)
        package.add("demo", _manifest("other"))
        loader = ModuleLoader(lambda manifest: None, package=package.package)

        candidates = loader.discover(log=lambda message: None)

        self.assertEqual(candidates, {})
        self.assertEqual(loader.skipped["demo"][0].kind, "id_mismatch")

    def test_untrusted_module_is_not_imported(self):
        package = _TempModules(self)
        marker = package.root / "untrusted.marker"
        package.add(
            "demo",
            _manifest("demo", trust="untrusted"),
            source=(
                "from pathlib import Path\n"
                "Path(%r).write_text('yes', encoding='utf-8')\n"
                "from sdk import ModuleBase\n"
                "class Demo(ModuleBase):\n"
                "    pass\n"
                "MODULE = Demo()\n" % str(marker)
            ),
        )
        loader = ModuleLoader(lambda manifest: None, package=package.package)

        candidates = loader.discover(log=lambda message: None)

        self.assertEqual(candidates, {})
        self.assertEqual(loader.skipped["demo"][0].kind, "trust_untrusted")
        self.assertFalse(marker.exists())

    def test_platform_mismatch_is_skipped(self):
        package = _TempModules(self)
        package.add("demo", _manifest("demo", platforms=["unknown_os"]))
        loader = ModuleLoader(lambda manifest: None, package=package.package)

        candidates = loader.discover(log=lambda message: None)

        self.assertEqual(candidates, {})
        self.assertEqual(
            loader.skipped["demo"][0].kind,
            "platform_unsupported",
        )

    def test_sdk_incompatible_module_is_skipped(self):
        package = _TempModules(self)
        package.add("demo", _manifest("demo", sdk={"min": "99.0.0"}))
        loader = ModuleLoader(lambda manifest: None, package=package.package)

        candidates = loader.discover(log=lambda message: None)

        self.assertEqual(candidates, {})
        self.assertEqual(loader.skipped["demo"][0].kind, "sdk_incompatible")

    def test_entrypoint_requires_explicit_module_attribute(self):
        package = _TempModules(self)
        package.add(
            "demo",
            _manifest("demo"),
            source=(
                "from sdk import ModuleBase\n"
                "class Demo(ModuleBase):\n"
                "    pass\n"
                "OTHER = Demo()\n"
            ),
        )
        loader = ModuleLoader(lambda manifest: None, package=package.package)
        logs = []

        candidates = loader.discover(log=logs.append)

        self.assertIn("demo", candidates)
        self.assertFalse(
            loader._import_entry(candidates["demo"], logs.append)
        )
        record = loader.records["demo"]
        self.assertEqual(record.state.value, "failed")
        self.assertEqual(record.phase, "import")
        self.assertTrue(
            any("FAILED (фаза import)" in message for message in logs)
        )

    def test_create_module_is_not_supported(self):
        package = _TempModules(self)
        package.add(
            "demo",
            _manifest("demo"),
            source=(
                "from sdk import ModuleBase\n"
                "class Demo(ModuleBase):\n"
                "    pass\n"
                "def create_module():\n"
                "    return Demo()\n"
            ),
        )
        loader = ModuleLoader(lambda manifest: None, package=package.package)

        candidates = loader.discover(log=lambda message: None)

        self.assertIn("demo", candidates)
        self.assertFalse(
            loader._import_entry(candidates["demo"], lambda message: None)
        )
        self.assertEqual(loader.records["demo"].phase, "import")

    def test_discover_does_not_import_entrypoint(self):
        package = _TempModules(self)
        marker = package.root / "discover-import.marker"
        package.add(
            "demo",
            _manifest("demo"),
            source=(
                "from pathlib import Path\n"
                "Path(%r).write_text('yes', encoding='utf-8')\n"
                "from sdk import ModuleBase\n"
                "class Demo(ModuleBase):\n"
                "    pass\n"
                "MODULE = Demo()\n" % str(marker)
            ),
        )
        loader = ModuleLoader(lambda manifest: None, package=package.package)

        candidates = loader.discover(log=lambda message: None)

        self.assertIn("demo", candidates)
        self.assertFalse(marker.exists())
        self.assertTrue(
            loader._import_entry(candidates["demo"], lambda message: None)
        )
        self.assertTrue(marker.exists())


class PreImportValidationTest(unittest.TestCase):
    """Код модуля не импортируется, если он будет SKIPPED по ADR-31."""

    @staticmethod
    def _source(marker):
        return (
            "from pathlib import Path\n"
            "Path(%r).write_text('yes', encoding='utf-8')\n"
            "from sdk import ModuleBase\n"
            "class Demo(ModuleBase):\n"
            "    pass\n"
            "MODULE = Demo()\n" % str(marker)
        )

    def test_missing_required_module_is_skipped_before_import(self):
        package = _TempModules(self)
        marker = package.root / "consumer.marker"
        package.add(
            "consumer",
            _manifest(
                "consumer",
                requires=[{"id": "absent"}],
            ),
            source=self._source(marker),
        )
        loader = ModuleLoader(lambda manifest: None, package=package.package)

        loader.discover_and_load(log=lambda message: None)

        self.assertFalse(marker.exists())
        self.assertEqual(
            loader.skipped["consumer"][0].kind,
            "missing_required_module",
        )
        self.assertEqual(
            loader.records["consumer"].state.value,
            "skipped",
        )

    def test_missing_capability_is_skipped_before_import(self):
        package = _TempModules(self)
        marker = package.root / "consumer.marker"
        package.add(
            "consumer",
            _manifest(
                "consumer",
                needs=[{"name": "absent.cap", "min_version": "1.0.0"}],
            ),
            source=self._source(marker),
        )
        loader = ModuleLoader(lambda manifest: None, package=package.package)

        loader.discover_and_load(log=lambda message: None)

        self.assertFalse(marker.exists())
        self.assertEqual(
            loader.skipped["consumer"][0].kind,
            "missing_capability",
        )

    def test_capability_version_is_checked_before_import(self):
        package = _TempModules(self)
        provider_marker = package.root / "provider.marker"
        consumer_marker = package.root / "consumer.marker"
        package.add(
            "provider",
            _manifest(
                "provider",
                provides=[{"name": "demo.cap", "version": "1.5.0"}],
            ),
            source=self._source(provider_marker),
        )
        package.add(
            "consumer",
            _manifest(
                "consumer",
                needs=[{"name": "demo.cap", "min_version": "2.0.0"}],
            ),
            source=self._source(consumer_marker),
        )
        loader = ModuleLoader(lambda manifest: None, package=package.package)

        loader.discover_and_load(log=lambda message: None)

        self.assertTrue(provider_marker.exists())
        self.assertFalse(consumer_marker.exists())
        self.assertEqual(
            loader.skipped["consumer"][0].kind,
            "capability_version",
        )

    def test_capability_conflict_skips_both_providers_before_import(self):
        package = _TempModules(self)
        markers = {
            name: package.root / ("%s.marker" % name)
            for name in ("first", "second", "consumer")
        }
        for name in ("first", "second"):
            package.add(
                name,
                _manifest(
                    name,
                    provides=[{"name": "shared.cap", "version": "1.0.0"}],
                ),
                source=self._source(markers[name]),
            )
        package.add(
            "consumer",
            _manifest(
                "consumer",
                needs=[{"name": "shared.cap", "min_version": "1.0.0"}],
            ),
            source=self._source(markers["consumer"]),
        )
        loader = ModuleLoader(lambda manifest: None, package=package.package)

        loader.discover_and_load(log=lambda message: None)

        for marker in markers.values():
            self.assertFalse(marker.exists())
        for name in ("first", "second", "consumer"):
            self.assertEqual(
                loader.skipped[name][0].kind,
                "capability_conflict",
            )

    def test_trust_policy_rejects_self_declared_trusted(self):
        package = _TempModules(self)
        marker = package.root / "demo.marker"
        package.add(
            "demo",
            _manifest("demo", trust="trusted"),
            source=self._source(marker),
        )
        loader = ModuleLoader(
            lambda manifest: None,
            package=package.package,
            trusted_ids=frozenset({"another_module"}),
        )

        loader.discover_and_load(log=lambda message: None)

        self.assertFalse(marker.exists())
        self.assertEqual(
            loader.skipped["demo"][0].kind,
            "trust_untrusted",
        )

    def test_trust_policy_allows_shipped_id(self):
        package = _TempModules(self)
        marker = package.root / "demo.marker"
        package.add(
            "demo",
            _manifest("demo", trust="trusted"),
            source=self._source(marker),
        )
        loader = ModuleLoader(
            lambda manifest: None,
            package=package.package,
            trusted_ids=frozenset({"demo"}),
        )

        loader.discover_and_load(log=lambda message: None)

        self.assertTrue(marker.exists())
        self.assertEqual(loader.records["demo"].state.value, "registered")

    def test_import_failure_leaves_failed_record(self):
        package = _TempModules(self)
        package.add(
            "demo",
            _manifest("demo"),
            source="raise RuntimeError('boom')\n",
        )
        loader = ModuleLoader(lambda manifest: None, package=package.package)
        logs = []

        loader.discover_and_load(log=logs.append)

        record = loader.records["demo"]
        self.assertEqual(record.state.value, "failed")
        self.assertEqual(record.phase, "import")
        self.assertIn("boom", record.error)
        self.assertTrue(
            any("FAILED (фаза import)" in line for line in logs)
        )


class _InitFailure:
    def init(self, _ctx):
        raise RuntimeError("init failed")

    def deactivate(self, _ctx):
        pass


class LoaderStateTest(unittest.TestCase):
    def test_states_follow_manifest_lifecycle(self):
        package = _TempModules(self)
        package.add("demo", _manifest("demo"))
        loader = ModuleLoader(
            lambda manifest: SimpleNamespace(),
            package=package.package,
        )

        candidates = loader.discover(log=lambda message: None)
        record = candidates["demo"]
        self.assertEqual(record.state, ModuleState.DISCOVERED)
        self.assertEqual(
            loader.describe()["demo"]["state"],
            "discovered",
        )

        ordered = loader._dependency_order(
            candidates,
            lambda message: None,
        )
        self.assertEqual(ordered, [record])
        self.assertEqual(record.state, ModuleState.VALIDATED)

        self.assertTrue(
            loader._import_entry(record, lambda message: None)
        )
        self.assertEqual(record.state, ModuleState.LOADED)

        self.assertTrue(
            loader._start_module(record, candidates, lambda message: None)
        )
        self.assertEqual(record.state, ModuleState.REGISTERED)

        loader.activate_all(log=lambda message: None)
        self.assertEqual(record.state, ModuleState.ACTIVE)

        loader.deactivate_all(log=lambda message: None)
        self.assertEqual(record.state, ModuleState.UNLOADED)


class ModuleConfigIntegrationTest(unittest.TestCase):
    @staticmethod
    def _write_config(path, modules):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({
                "schema_version": 1,
                "modules": modules,
            }),
            encoding="utf-8",
        )

    def test_disabled_module_is_not_imported_and_can_be_enabled(self):
        package = _TempModules(self)
        marker = package.root / "demo.marker"
        package.add(
            "demo",
            _manifest("demo"),
            source=PreImportValidationTest._source(marker),
        )
        config_path = package.root / "config" / "modules.json"
        self._write_config(config_path, {"demo": {"enabled": False}})
        store = ModuleConfigStore(config_path)
        loader = ModuleLoader(
            lambda manifest: SimpleNamespace(),
            package=package.package,
            config=store,
        )

        loader.discover_and_load(log=lambda message: None)

        self.assertFalse(marker.exists())
        record = loader.records["demo"]
        self.assertEqual(record.state, ModuleState.DISABLED)
        self.assertEqual(loader._order, [])

        loader.activate_all(log=lambda message: None)
        self.assertTrue(loader.set_enabled("demo", True))

        self.assertTrue(marker.exists())
        record = loader.records["demo"]
        self.assertEqual(record.state, ModuleState.ACTIVE)
        self.assertTrue(store.enabled_for("demo", False))

    def test_disable_provider_cascades_and_reenable_restores_dependent(self):
        package = _TempModules(self)
        package.add("provider", _manifest("provider"))
        package.add(
            "dependent",
            _manifest(
                "dependent",
                requires=[{"id": "provider"}],
            ),
        )
        package.add(
            "leaf",
            _manifest(
                "leaf",
                requires=[{"id": "dependent"}],
            ),
        )
        config_path = package.root / "config" / "modules.json"
        store = ModuleConfigStore(config_path)
        loader = ModuleLoader(
            lambda manifest: SimpleNamespace(),
            package=package.package,
            config=store,
        )
        loader.discover_and_load(log=lambda message: None)
        loader.activate_all(log=lambda message: None)
        self.assertEqual(
            loader.records["provider"].state,
            ModuleState.ACTIVE,
        )
        self.assertEqual(
            loader.records["dependent"].state,
            ModuleState.ACTIVE,
        )
        self.assertEqual(
            loader.records["leaf"].state,
            ModuleState.ACTIVE,
        )

        self.assertTrue(loader.set_enabled("provider", False))

        self.assertEqual(
            loader.records["provider"].state,
            ModuleState.DISABLED,
        )
        for name in ("dependent", "leaf"):
            record = loader.records[name]
            self.assertEqual(record.state, ModuleState.SKIPPED)
            self.assertEqual(
                loader.skipped[name][0].kind,
                "provider_unloaded",
            )
        self.assertFalse(store.enabled_for("provider", True))

        self.assertTrue(loader.set_enabled("provider", True))

        self.assertEqual(
            loader.records["provider"].state,
            ModuleState.ACTIVE,
        )
        for name in ("dependent", "leaf"):
            self.assertEqual(
                loader.records[name].state,
                ModuleState.ACTIVE,
            )


    def test_optional_provider_is_ordered_before_consumer(self):
        package = _TempModules(self)
        package.add(
            "zprovider",
            _manifest(
                "zprovider",
                provides=[{"name": "demo.cap", "version": "1.0.0"}],
            ),
            source=(
                "from sdk import ModuleBase\n"
                "class Demo(ModuleBase):\n"
                "    def register(self, ctx):\n"
                "        ctx.capabilities.provide(\n"
                "            'demo.cap', '1.0.0', object()\n"
                "        )\n"
                "MODULE = Demo()\n"
            ),
        )
        package.add(
            "aconsumer",
            _manifest(
                "aconsumer",
                optional_requires=[{"id": "zprovider"}],
                optional_needs=[
                    {"name": "demo.cap", "min_version": "1.0.0"}
                ],
            ),
            source=(
                "from sdk import ModuleBase\n"
                "class Demo(ModuleBase):\n"
                "    def register(self, ctx):\n"
                "        self.provider = ctx.capabilities.optional(\n"
                "            'demo.cap', '1.0.0'\n"
                "        )\n"
                "MODULE = Demo()\n"
            ),
        )
        loader = ModuleLoader(
            lambda manifest: SimpleNamespace(),
            package=package.package,
        )

        loader.discover_and_load(log=lambda message: None)

        self.assertEqual(loader._order, ["zprovider", "aconsumer"])
        self.assertIsNotNone(
            loader.records["aconsumer"].instance.provider
        )

    def test_optional_dependent_is_reactivated_degraded(self):
        package = _TempModules(self)
        package.add(
            "aprovider",
            _manifest(
                "aprovider",
                provides=[{"name": "demo.cap", "version": "1.0.0"}],
            ),
            source=(
                "from sdk import ModuleBase\n"
                "class Demo(ModuleBase):\n"
                "    def register(self, ctx):\n"
                "        ctx.capabilities.provide(\n"
                "            'demo.cap', '1.0.0', object()\n"
                "        )\n"
                "MODULE = Demo()\n"
            ),
        )
        package.add(
            "optional",
            _manifest(
                "optional",
                optional_requires=[{"id": "aprovider"}],
                optional_needs=[
                    {"name": "demo.cap", "min_version": "1.0.0"}
                ],
            ),
            source=(
                "from sdk import ModuleBase\n"
                "class Demo(ModuleBase):\n"
                "    def register(self, ctx):\n"
                "        self.provider = ctx.capabilities.optional(\n"
                "            'demo.cap', '1.0.0'\n"
                "        )\n"
                "MODULE = Demo()\n"
            ),
        )
        config_path = package.root / "config" / "modules.json"
        store = ModuleConfigStore(config_path)
        loader = ModuleLoader(
            lambda manifest: SimpleNamespace(),
            package=package.package,
            config=store,
        )
        loader.discover_and_load(log=lambda message: None)
        loader.activate_all(log=lambda message: None)
        self.assertEqual(
            loader.records["optional"].optional_dependencies,
            ("aprovider",),
        )
        self.assertIsNotNone(
            loader.records["optional"].instance.provider
        )

        self.assertTrue(loader.set_enabled("aprovider", False))

        self.assertEqual(
            loader.records["aprovider"].state,
            ModuleState.DISABLED,
        )
        optional = loader.records["optional"]
        self.assertEqual(optional.state, ModuleState.ACTIVE)
        self.assertIsNone(optional.instance.provider)
        self.assertNotIn("provider_unloaded", [
            skip.kind for skip in loader.skipped.get("optional", ())
        ])

        self.assertTrue(loader.set_enabled("aprovider", True))
        optional = loader.records["optional"]
        self.assertEqual(optional.state, ModuleState.ACTIVE)
        self.assertIsNotNone(optional.instance.provider)
        optional_instance = optional.instance
        self.assertTrue(loader.reload("aprovider", log=lambda message: None))
        optional = loader.records["optional"]
        self.assertEqual(optional.state, ModuleState.ACTIVE)
        self.assertIsNot(optional.instance, optional_instance)
        self.assertIsNotNone(optional.instance.provider)


class CapabilityRegistrationTest(unittest.TestCase):
    def test_provider_and_consumer_use_capabilities_api(self):
        package = _TempModules(self)
        package.add(
            "provider",
            _manifest(
                "provider",
                provides=[{"name": "demo.cap", "version": "1.0.0"}],
            ),
            source=(
                "from sdk import ModuleBase\n"
                "class Demo(ModuleBase):\n"
                "    def register(self, ctx):\n"
                "        ctx.capabilities.provide(\n"
                "            'demo.cap', '1.0.0', {'value': 42}\n"
                "        )\n"
                "MODULE = Demo()\n"
            ),
        )
        package.add(
            "consumer",
            _manifest(
                "consumer",
                needs=[{"name": "demo.cap", "min_version": "1.0.0"}],
            ),
            source=(
                "from sdk import ModuleBase\n"
                "class Demo(ModuleBase):\n"
                "    def register(self, ctx):\n"
                "        self.value = ctx.capabilities.require(\n"
                "            'demo.cap', '1.0.0'\n"
                "        )['value']\n"
                "MODULE = Demo()\n"
            ),
        )
        loader = ModuleLoader(
            lambda manifest: SimpleNamespace(),
            package=package.package,
        )

        loader.discover_and_load(log=lambda message: None)

        self.assertEqual(loader.records["consumer"].instance.value, 42)
        self.assertEqual(
            loader.capability_registry.provider("demo.cap"),
            "provider",
        )

    def test_missing_declared_provide_fails_register(self):
        package = _TempModules(self)
        package.add(
            "provider",
            _manifest(
                "provider",
                provides=[{"name": "demo.cap", "version": "1.0.0"}],
            ),
            source=(
                "from sdk import ModuleBase\n"
                "class Demo(ModuleBase):\n"
                "    pass\n"
                "MODULE = Demo()\n"
            ),
        )
        loader = ModuleLoader(
            lambda manifest: SimpleNamespace(),
            package=package.package,
        )

        loader.discover_and_load(log=lambda message: None)

        record = loader.records["provider"]
        self.assertEqual(record.state, ModuleState.FAILED)
        self.assertEqual(record.phase, "register")
        self.assertIn("не предоставил", record.error)
        self.assertFalse(loader.capability_registry.has("demo.cap"))

    def test_wrong_provide_version_fails_register(self):
        package = _TempModules(self)
        package.add(
            "provider",
            _manifest(
                "provider",
                provides=[{"name": "demo.cap", "version": "1.0.0"}],
            ),
            source=(
                "from sdk import ModuleBase\n"
                "class Demo(ModuleBase):\n"
                "    def register(self, ctx):\n"
                "        ctx.capabilities.provide(\n"
                "            'demo.cap', '2.0.0', object()\n"
                "        )\n"
                "MODULE = Demo()\n"
            ),
        )
        loader = ModuleLoader(
            lambda manifest: SimpleNamespace(),
            package=package.package,
        )

        loader.discover_and_load(log=lambda message: None)

        record = loader.records["provider"]
        self.assertEqual(record.state, ModuleState.FAILED)
        self.assertEqual(record.phase, "register")
        self.assertIn("не совпадает", record.error)

    def test_undeclared_provide_fails_register(self):
        package = _TempModules(self)
        package.add(
            "provider",
            _manifest("provider"),
            source=(
                "from sdk import ModuleBase\n"
                "class Demo(ModuleBase):\n"
                "    def register(self, ctx):\n"
                "        ctx.capabilities.provide(\n"
                "            'extra.cap', '1.0.0', object()\n"
                "        )\n"
                "MODULE = Demo()\n"
            ),
        )
        loader = ModuleLoader(
            lambda manifest: SimpleNamespace(),
            package=package.package,
        )

        loader.discover_and_load(log=lambda message: None)

        record = loader.records["provider"]
        self.assertEqual(record.state, ModuleState.FAILED)
        self.assertEqual(record.phase, "register")
        self.assertIn("не объявлял", record.error)

    def test_provide_outside_register_fails_init(self):
        package = _TempModules(self)
        package.add(
            "provider",
            _manifest(
                "provider",
                provides=[{"name": "demo.cap", "version": "1.0.0"}],
            ),
            source=(
                "from sdk import ModuleBase\n"
                "class Demo(ModuleBase):\n"
                "    def init(self, ctx):\n"
                "        super().init(ctx)\n"
                "        ctx.capabilities.provide(\n"
                "            'demo.cap', '1.0.0', object()\n"
                "        )\n"
                "MODULE = Demo()\n"
            ),
        )
        loader = ModuleLoader(
            lambda manifest: SimpleNamespace(),
            package=package.package,
        )

        loader.discover_and_load(log=lambda message: None)

        record = loader.records["provider"]
        self.assertEqual(record.state, ModuleState.FAILED)
        self.assertEqual(record.phase, "init")
        self.assertIn("только в register", record.error)

    def test_optional_capability_returns_none_in_degraded_mode(self):
        package = _TempModules(self)
        package.add(
            "consumer",
            _manifest(
                "consumer",
                optional_needs=[
                    {"name": "absent.cap", "min_version": "1.0.0"}
                ],
            ),
            source=(
                "from sdk import ModuleBase\n"
                "class Demo(ModuleBase):\n"
                "    def register(self, ctx):\n"
                "        self.value = ctx.capabilities.optional(\n"
                "            'absent.cap', '1.0.0'\n"
                "        )\n"
                "MODULE = Demo()\n"
            ),
        )
        loader = ModuleLoader(
            lambda manifest: SimpleNamespace(),
            package=package.package,
        )

        loader.discover_and_load(log=lambda message: None)

        record = loader.records["consumer"]
        self.assertEqual(record.state, ModuleState.REGISTERED)
        self.assertIsNone(record.instance.value)

    def test_runtime_provider_conflict_rejects_new_provider(self):
        package = _TempModules(self)
        package.add(
            "one",
            _manifest(
                "one",
                provides=[{"name": "shared.cap", "version": "1.0.0"}],
            ),
            source=(
                "from sdk import ModuleBase\n"
                "class Demo(ModuleBase):\n"
                "    def register(self, ctx):\n"
                "        ctx.capabilities.provide(\n"
                "            'shared.cap', '1.0.0', object()\n"
                "        )\n"
                "MODULE = Demo()\n"
            ),
        )
        marker = package.root / "two.marker"
        package.add(
            "two",
            _manifest(
                "two",
                provides=[{"name": "shared.cap", "version": "1.0.0"}],
            ),
            source=(
                "from pathlib import Path\n"
                "Path(%r).write_text('yes', encoding='utf-8')\n"
                "from sdk import ModuleBase\n"
                "class Demo(ModuleBase):\n"
                "    def register(self, ctx):\n"
                "        ctx.capabilities.provide(\n"
                "            'shared.cap', '1.0.0', object()\n"
                "        )\n"
                "MODULE = Demo()\n" % str(marker)
            ),
        )
        config_path = package.root / "config" / "modules.json"
        ModuleConfigStore(config_path).set_enabled("two", False)
        loader = ModuleLoader(
            lambda manifest: SimpleNamespace(),
            package=package.package,
            config=ModuleConfigStore(config_path),
        )
        loader.discover_and_load(log=lambda message: None)
        loader.activate_all(log=lambda message: None)

        self.assertFalse(marker.exists())
        self.assertFalse(loader.set_enabled("two", True))

        self.assertFalse(marker.exists())
        record = loader.records["two"]
        self.assertEqual(record.state, ModuleState.SKIPPED)
        self.assertEqual(loader.skipped["two"][0].kind, "capability_conflict")

        self.assertTrue(loader.set_enabled("one", False))
        self.assertTrue(loader.set_enabled("two", True))
        self.assertTrue(marker.exists())
        self.assertEqual(
            loader.records["two"].state,
            ModuleState.ACTIVE,
        )


class RequiredModuleTest(unittest.TestCase):
    def _loader(self, calls):
        class Instance:
            def deactivate(self, _ctx):
                calls.append("deactivate")

        loader = ModuleLoader(
            lambda manifest: None,
            required_ids=frozenset({"base"}),
        )
        record = ModuleRecord(
            manifest=ModuleManifest(id="base", name="base"),
            instance=Instance(),
            package="base",
            ctx=SimpleNamespace(),
            state=ModuleState.ACTIVE,
        )
        loader.records["base"] = record
        loader._order.append("base")
        return loader, record

    def test_required_module_cannot_be_unloaded_or_reloaded(self):
        calls = []
        loader, record = self._loader(calls)

        self.assertFalse(loader.unload("base", force=True))
        self.assertEqual(record.state, ModuleState.ACTIVE)
        self.assertEqual(calls, [])

        self.assertFalse(loader.reload("base"))
        self.assertEqual(record.state, ModuleState.ACTIVE)
        self.assertEqual(calls, [])

    def test_required_module_is_unloaded_by_shutdown_path(self):
        calls = []
        loader, record = self._loader(calls)

        loader.deactivate_all(log=lambda message: None)

        self.assertEqual(record.state, ModuleState.UNLOADED)
        self.assertEqual(calls, ["deactivate"])


class ModuleLoaderTest(unittest.TestCase):
    def test_builtin_discovery_reads_all_shipped_manifests(self):
        loader = ModuleLoader(lambda manifest: None)

        candidates = loader.discover(log=lambda message: None)

        self.assertEqual(
            set(candidates),
            {"base", "item_browser", "ogg_editor"},
        )
        self.assertEqual(loader.skipped, {})

    def test_base_is_always_loaded_even_if_config_disables_it(self):
        package = _TempModules(self)
        config_path = package.root / "config" / "modules.json"
        ModuleConfigStore(config_path).set_enabled("base", False)
        logs = []
        loader = ModuleLoader(
            lambda manifest: None,
            trusted_ids=SHIPPED_MODULE_IDS,
            required_ids=REQUIRED_MODULE_IDS,
            config=ModuleConfigStore(config_path),
        )

        candidates = loader.discover(log=logs.append)

        self.assertIn("base", candidates)
        self.assertTrue(
            any("обязательный модуль нельзя отключить" in line
                for line in logs)
        )
        self.assertFalse(loader.set_enabled("base", False))

    def test_item_browser_can_be_disabled_independently(self):
        package = _TempModules(self)
        config_path = package.root / "config" / "modules.json"
        ModuleConfigStore(config_path).set_enabled("item_browser", False)
        loader = ModuleLoader(
            lambda manifest: None,
            trusted_ids=SHIPPED_MODULE_IDS,
            required_ids=REQUIRED_MODULE_IDS,
            config=ModuleConfigStore(config_path),
        )

        candidates = loader.discover(log=lambda message: None)

        self.assertIn("base", candidates)
        self.assertNotIn("item_browser", candidates)
        self.assertEqual(
            loader.records["item_browser"].state,
            ModuleState.DISABLED,
        )

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
        self.assertIn("base", {item.id for item in manifests})

    def test_dependencies_are_loaded_before_dependents(self):
        loader = ModuleLoader(lambda manifest: None)
        candidates = {
            "base": _Entry("base", package="base"),
            "z": _Entry("z", ("base",), package="z_module"),
            "a": _Entry("a", ("z",), package="a_module"),
        }
        ordered = loader._dependency_order(candidates, lambda _m: None)
        self.assertEqual(
            [entry.manifest.id for entry in ordered],
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
        self.assertEqual(
            loader.describe()["a"]["state"],
            "skipped",
        )
        self.assertEqual(
            loader.describe()["b"]["state"],
            "skipped",
        )
        self.assertEqual(
            loader.skipped["a"][0].kind,
            "cycle",
        )

    def test_register_failure_rolls_back_and_leaves_failed_record(self):
        loader = ModuleLoader(lambda manifest: object())
        entry = _Entry("broken")
        entry.instance = _Lifecycle()
        logs = []
        loader._start_module(entry, {}, logs.append)

        record = loader.records.get("broken")
        self.assertIsNotNone(record)
        self.assertEqual(record.state.value, "failed")
        self.assertEqual(record.phase, "register")
        self.assertIn("register failed", record.error)
        self.assertEqual(loader._order, [])
        self.assertTrue(entry.instance.init_called)
        self.assertTrue(entry.instance.register_called)
        self.assertTrue(entry.instance.deactivate_called)
        self.assertTrue(any("FAILED (фаза register)" in line for line in logs))

    def test_init_failure_leaves_failed_record(self):
        loader = ModuleLoader(lambda manifest: object())
        entry = _Entry("broken")
        entry.instance = _InitFailure()
        logs = []

        loader._start_module(entry, {}, logs.append)

        record = loader.records.get("broken")
        self.assertIsNotNone(record)
        self.assertEqual(record.state.value, "failed")
        self.assertEqual(record.phase, "init")
        self.assertIn("init failed", record.error)
        self.assertEqual(loader._order, [])
        self.assertTrue(any("FAILED (фаза init)" in line for line in logs))

    def test_activate_and_deactivate_do_not_raise(self):
        loader = ModuleLoader(lambda manifest: None)
        with redirect_stderr(io.StringIO()):
            loader.discover_and_load(log=lambda message: None)
            loader.activate_all(log=lambda message: None)
            loader.deactivate_all(log=lambda message: None)


class DependencyIdentityTest(unittest.TestCase):
    def test_requirement_uses_module_id(self):
        loader = ModuleLoader(lambda manifest: None)
        candidates = {
            "base": _Entry("base"),
            "consumer": _Entry(
                "consumer",
                (ModuleRequirement("base", "0.1.0"),),
            ),
        }

        ordered = loader._dependency_order(candidates, lambda _m: None)

        self.assertEqual(
            [entry.manifest.id for entry in ordered],
            ["base", "consumer"],
        )

    def test_capability_dto_is_used_by_manifest(self):
        manifest = ModuleManifest(
            id="demo",
            name="Demo",
            provides=(CapabilityDeclaration("demo.cap"),),
            needs=(CapabilityRequirement("other.cap"),),
        )

        self.assertEqual(manifest.provides[0].name, "demo.cap")
        self.assertEqual(manifest.provides[0].version, "1.0.0")
        self.assertEqual(manifest.needs[0].name, "other.cap")
        self.assertEqual(manifest.needs[0].min_version, "1.0.0")


if __name__ == "__main__":
    unittest.main()
