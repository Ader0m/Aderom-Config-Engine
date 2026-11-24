# -*- coding: utf-8 -*-
"""Жизненный цикл модулей: версии, optional/capabilities, unload/reload."""

import importlib
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.qt_support import qapp
from tests.support import SRC  # noqa: F401

from sdk import ModuleRequirement, RegistrationHandle, RegistrationScope
from sdk.kernel import ModuleContext, ModuleManifest
from ui.platform.sdk_bridge import build_module_context
from ui.bus import CommandBus, EventBus
from ui.module_loader import (
    ModuleLoader,
    ModuleRecord,
    ModuleSkip,
    ModuleState,
)
from ui.platform_api import PlatformApi


class _Manifest:
    def __init__(
        self,
        name,
        version="0.1.0",
        requires=(),
        optional_requires=(),
        provides=(),
        needs=(),
        optional_needs=(),
        package=None,
    ):
        self.name = name
        self.version = version
        self.sdk_min = "0.1.0"
        self.requires = tuple(requires)
        self.optional_requires = tuple(optional_requires)
        self.provides = tuple(provides)
        self.needs = tuple(needs)
        self.optional_needs = tuple(optional_needs)
        self.package = package or name


class _Entry:
    def __init__(self, name, package=None, **kwargs):
        self.manifest = _Manifest(name, package=package, **kwargs)
        self.instance = object()
        self.ctx = None
        self.package = package or name
        self.dependencies = ()


class _Instance:
    def __init__(self, fail_activate=False):
        self.fail_activate = fail_activate
        self.activated = False
        self.deactivated = False

    def activate(self, ctx):
        if self.fail_activate:
            raise RuntimeError("activate failed")
        self.activated = True

    def deactivate(self, ctx):
        self.deactivated = True


class _Context:
    def __init__(self, name, calls):
        self.registrations = RegistrationScope(name)
        self.registrations.track(
            RegistrationHandle(lambda: calls.append(name))
        )
        self.ui = None
        self.tasks = None


class ManifestResolutionTest(unittest.TestCase):
    def test_versions_optional_and_capabilities(self):
        loader = ModuleLoader(lambda manifest: None)
        candidates = {
            "base": _Entry(
                "base",
                version="0.4.0",
                provides=("demo.cap",),
                package="a_base",
            ),
            "needs_cap": _Entry(
                "needs_cap",
                needs=("demo.cap",),
                package="b_cap",
            ),
            "optional": _Entry(
                "optional",
                optional_requires=("absent",),
                package="c_optional",
            ),
            "too_new": _Entry(
                "too_new",
                requires=(ModuleRequirement("base", "0.5.0"),),
                package="d_too_new",
            ),
            "no_cap": _Entry(
                "no_cap",
                needs=("absent.cap",),
                package="e_no_cap",
            ),
        }

        ordered = loader._dependency_order(candidates, lambda message: None)

        self.assertEqual(
            [entry.manifest.name for entry in ordered],
            ["base", "needs_cap", "optional"],
        )
        self.assertEqual(
            loader.skipped["too_new"][0].kind,
            "version_conflict",
        )
        self.assertEqual(
            loader.skipped["no_cap"][0].kind,
            "missing_capability",
        )

    def test_capability_conflict_is_not_silently_resolved(self):
        loader = ModuleLoader(lambda manifest: None)
        logs = []
        candidates = {
            "first": _Entry(
                "first",
                provides=("shared.cap",),
                package="a_first",
            ),
            "second": _Entry(
                "second",
                provides=("shared.cap",),
                package="b_second",
            ),
            "consumer": _Entry(
                "consumer",
                needs=("shared.cap",),
                package="c_consumer",
            ),
        }

        ordered = loader._dependency_order(candidates, logs.append)

        self.assertNotIn("shared.cap", loader.capabilities)
        self.assertEqual(
            set(loader.capability_conflicts["shared.cap"]),
            {"first", "second"},
        )
        self.assertNotIn(
            "consumer",
            [entry.manifest.name for entry in ordered],
        )
        self.assertEqual(
            loader.skipped["consumer"][0].kind,
            "missing_capability",
        )
        self.assertTrue(
            any("Конфликт capability" in message for message in logs)
        )

    def test_missing_required_skips_dependent(self):
        loader = ModuleLoader(lambda manifest: None)
        candidates = {
            "base": _Entry("base", package="a_base"),
            "middle": _Entry(
                "middle",
                requires=(ModuleRequirement("missing"),),
                package="b_middle",
            ),
            "top": _Entry(
                "top",
                requires=("middle",),
                package="c_top",
            ),
        }

        ordered = loader._dependency_order(candidates, lambda message: None)

        self.assertEqual([entry.manifest.name for entry in ordered], ["base"])
        self.assertEqual(
            loader.skipped["middle"][0].kind,
            "missing_required",
        )
        self.assertEqual(
            loader.skipped["top"][0].kind,
            "dependency_skipped",
        )


class ActivateFailureTest(unittest.TestCase):
    def _record(self, name, calls, fail=False, dependencies=(), provides=()):
        manifest = ModuleManifest(name=name, provides=tuple(provides))
        instance = _Instance(fail_activate=fail)
        record = ModuleRecord(
            manifest=manifest,
            instance=instance,
            package="",
            ctx=_Context(name, calls),
            state=ModuleState.REGISTERED,
            dependencies=tuple(dependencies),
        )
        return record

    def test_failed_activate_is_deactivated_and_removed(self):
        calls = []
        loader = ModuleLoader(lambda manifest: None)
        record = self._record("bad", calls, fail=True)
        loader.records["bad"] = record
        loader._order.append("bad")

        loader.activate_all(log=lambda message: None)

        self.assertTrue(record.instance.deactivated)
        self.assertTrue(record.ctx.registrations.closed)
        self.assertIsNone(loader.records.get("bad"))
        self.assertIn("bad", calls)

    def test_dependent_of_failed_module_is_rolled_back(self):
        calls = []
        loader = ModuleLoader(lambda manifest: None)
        bad = self._record("bad", calls, fail=True)
        dependent = self._record(
            "dependent",
            calls,
            dependencies=("bad",),
        )
        for record in (bad, dependent):
            loader.records[record.name] = record
            loader._order.append(record.name)

        loader.activate_all(log=lambda message: None)

        self.assertTrue(dependent.instance.deactivated)
        self.assertTrue(dependent.ctx.registrations.closed)
        self.assertEqual(loader.records, {})

    def test_failed_activate_removes_own_capability(self):
        calls = []
        loader = ModuleLoader(lambda manifest: None)
        record = self._record(
            "bad",
            calls,
            fail=True,
            provides=("bad.cap",),
        )
        loader.records["bad"] = record
        loader._order.append("bad")
        loader.capability_registry.provide(
            "bad.cap",
            "1.0.0",
            object(),
            owner="bad",
        )

        loader.activate_all(log=lambda message: None)

        self.assertFalse(loader.capability_registry.has("bad.cap"))
        self.assertNotIn("bad.cap", loader.capabilities)

    def test_deactivate_all_removes_capability(self):
        calls = []
        loader = ModuleLoader(lambda manifest: None)
        record = self._record(
            "good",
            calls,
            provides=("good.cap",),
        )
        record.state = ModuleState.ACTIVE
        loader.records["good"] = record
        loader._order.append("good")
        loader.capability_registry.provide(
            "good.cap",
            "1.0.0",
            object(),
            owner="good",
        )

        loader.deactivate_all(log=lambda message: None)

        self.assertEqual(record.state, ModuleState.UNLOADED)
        self.assertFalse(loader.capability_registry.has("good.cap"))
        self.assertNotIn("good.cap", loader.capabilities)


class PackageLifecycleTest(unittest.TestCase):
    """Реальный import/unload/reload на временном пакете модулей."""

    _counter = 0

    def setUp(self):
        self.app = qapp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

        PackageLifecycleTest._counter += 1
        self.package_name = "ace_lifecycle_%d" % self._counter
        self.addCleanup(self._purge_package)
        sys.path.insert(0, str(self.root))
        self.addCleanup(self._remove_path)

    def _remove_path(self):
        try:
            sys.path.remove(str(self.root))
        except ValueError:
            pass

    def _purge_package(self):
        prefix = self.package_name
        for name in list(sys.modules):
            if name == prefix or name.startswith(prefix + "."):
                sys.modules.pop(name, None)

    def _make_package(self, modules):
        package_dir = self.root / self.package_name
        package_dir.mkdir()
        (package_dir / "__init__.py").write_text("", encoding="utf-8")
        for module_name, source in modules.items():
            module_dir = package_dir / module_name
            module_dir.mkdir()
            (module_dir / "__init__.py").write_text(
                "from .module import MODULE\n",
                encoding="utf-8",
            )
            (module_dir / "module.py").write_text(
                textwrap.dedent(source),
                encoding="utf-8",
            )
        return importlib.import_module(self.package_name)

    def _make_context(self):
        events = EventBus()
        commands = CommandBus()
        paths = SimpleNamespace(
            projects_dir=self.root / "projects",
            app_dir=self.root,
        )
        api = PlatformApi(events, commands, None, object(), paths)

        def make_context(manifest):
            return build_module_context(
                manifest,
                sdk_version="0.1.0",
                events=events,
                commands=commands,
                ui=api,
                tasks=None,
                services=object(),
                paths=paths,
            )

        return api, events, commands, make_context

    def _demo_source(self):
        return """
        from sdk.kernel import ModuleBase, ModuleManifest
        from sdk.ui import WindowSpec
        from PyQt5.QtWidgets import QWidget

        class DemoModule(ModuleBase):
            manifest = ModuleManifest(
                name="demo",
                version="1.0.0",
                provides=("demo.cap",),
            )

            def init(self, ctx):
                super().init(ctx)
                self.activated = False
                self.deactivated = False
                self.window = None

            def register(self, ctx):
                self.menu = ctx.ui.register_menu(
                    "Инструменты", "Demo", lambda: None,
                )
                ctx.commands.register("demo.run", lambda: None)

            def activate(self, ctx):
                self.activated = True
                self.window = QWidget()
                ctx.ui.open_window(
                    WindowSpec(
                        key="demo.window",
                        title="Demo",
                        kind="floating",
                    ),
                    self.window,
                )

            def deactivate(self, ctx):
                self.deactivated = True

        MODULE = DemoModule()
        """

    def _dependent_source(self):
        return """
        from sdk.kernel import ModuleBase, ModuleManifest

        class DependentModule(ModuleBase):
            manifest = ModuleManifest(
                name="dependent",
                requires=("demo",),
            )

            def init(self, ctx):
                super().init(ctx)
                self.deactivated = False

            def register(self, ctx):
                ctx.commands.register("dependent.run", lambda: None)

            def deactivate(self, ctx):
                self.deactivated = True

        MODULE = DependentModule()
        """

    def test_unload_and_reload_removes_package_and_registrations(self):
        package = self._make_package({
            "demo": self._demo_source(),
        })
        api, _events, commands, make_context = self._make_context()
        loader = ModuleLoader(make_context, package=package)

        loader.discover_and_load(log=lambda message: None)
        loader.activate_all(log=lambda message: None)

        record = loader.records["demo"]
        old_instance = record.instance
        old_ctx = record.ctx
        self.assertEqual(record.state, ModuleState.ACTIVE)
        self.assertTrue(commands.has("demo.run"))
        self.assertTrue(api.menu_registrations)
        self.assertTrue(old_instance.window.isVisible())

        self.assertTrue(loader.reload("demo", log=lambda message: None))

        new_record = loader.records["demo"]
        self.assertIsNot(new_record.instance, old_instance)
        self.assertTrue(old_instance.deactivated)
        self.assertFalse(old_instance.window.isVisible())
        self.assertTrue(old_ctx.registrations.closed)
        self.assertEqual(new_record.state, ModuleState.ACTIVE)
        self.assertTrue(commands.has("demo.run"))
        self.assertTrue(api.menu_registrations)
        self.assertTrue(new_record.instance.window.isVisible())
        self.assertIn(
            self.package_name + ".demo.module",
            sys.modules,
        )

        self.assertTrue(loader.unload("demo", log=lambda message: None))
        self.assertFalse(new_record.instance.window.isVisible())
        self.assertNotIn("demo", loader.records)
        self.assertFalse(commands.has("demo.run"))
        self.assertEqual(api.menu_registrations, [])
        self.assertNotIn(
            self.package_name + ".demo",
            sys.modules,
        )

    def test_unload_refuses_until_dependent_is_forced(self):
        package = self._make_package({
            "demo": self._demo_source(),
            "dependent": self._dependent_source(),
        })
        _api, _events, commands, make_context = self._make_context()
        loader = ModuleLoader(make_context, package=package)

        loader.discover_and_load(log=lambda message: None)
        loader.activate_all(log=lambda message: None)

        self.assertFalse(loader.unload("demo", log=lambda message: None))
        self.assertIn("demo", loader.records)
        self.assertIn("dependent", loader.records)

        self.assertTrue(
            loader.unload("demo", log=lambda message: None, force=True)
        )
        self.assertNotIn("demo", loader.records)
        self.assertNotIn("dependent", loader.records)
        self.assertFalse(commands.has("demo.run"))
        self.assertFalse(commands.has("dependent.run"))


class ReleaseRuntimeTest(unittest.TestCase):
    def test_release_runtime_cancels_without_waiting(self):
        loader = ModuleLoader(lambda *_args, **_kwargs: None)
        calls = []

        class _Tasks:
            def cancel_owner(self):
                calls.append("cancel")
                return 2

            def wait_owner(self, *_args, **_kwargs):
                calls.append("wait")
                raise AssertionError("UI не должен ждать callbacks")

        ui = SimpleNamespace(
            release_owner=lambda: calls.append("release"),
        )
        ctx = SimpleNamespace(ui=ui, tasks=_Tasks())
        entry = SimpleNamespace(
            manifest=SimpleNamespace(name="demo"),
        )
        logs = []

        loader._release_runtime(entry, ctx, logs.append)

        self.assertEqual(calls, ["release", "cancel"])
        self.assertTrue(
            any("не блокирует" in message for message in logs)
        )


if __name__ == "__main__":
    unittest.main()
