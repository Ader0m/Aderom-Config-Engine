# -*- coding: utf-8 -*-
"""Точечная инвалидация кеша при правке файлов проекта."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.support import SRC  # noqa: F401

from sdk.api import ProjectOverlay

from core.facade import CoreServices
from core.resolver.service import ResolverService
from core.tools.cache import FileCacheSystem, ResolvedCache
from core.tree.builder import TreeBuilder
from ui.platform_api import PlatformApi


class FakeEvents:
    def subscribe(self, _name, _callback):
        return None

    def publish(self, _name, _payload=None):
        pass


class FakeCommands:
    def register(self, _name, _handler):
        pass


class _Forest:
    """Минимальный лес: корни и связи child -> parents."""

    def __init__(self, roots, include_parents=None):
        self.roots = [Path(path) for path in roots]
        self.roots_set = set(self.roots)
        self.files = list(self.roots)
        self.xml_files = []
        self.node_parent = {}
        self.include_parents = {
            Path(child): {Path(parent) for parent in parents}
            for child, parents in (include_parents or {}).items()
        }


class _Services:
    def __init__(self):
        self.invalidated = []
        self.full_invalidations = 0
        self.prebuild_starts = []

    def invalidate_paths(self, roots):
        self.invalidated.append(set(roots))

    def invalidate_resolver_cache(self):
        self.full_invalidations += 1

    def ensure_prebuild(self, overlay=None, roots=None):
        self.prebuild_starts.append(roots)


class WorkspaceRootsTest(unittest.TestCase):
    def test_shared_include_invalidates_every_root(self):
        root_a = Path("configs/a.ltx")
        root_b = Path("configs/b.ltx")
        shared = Path("configs/shared.ltx")
        forest = _Forest(
            [root_a, root_b],
            include_parents={shared: {root_a, root_b}},
        )

        api = PlatformApi(FakeEvents(), FakeCommands(), None, None, None)
        api.workspace.set_forest(forest)

        self.assertEqual(
            api.workspace.roots_for([shared]),
            {root_a, root_b},
        )

    def test_unknown_path_has_no_root(self):
        forest = _Forest([Path("configs/a.ltx")])
        api = PlatformApi(FakeEvents(), FakeCommands(), None, None, None)
        api.workspace.set_forest(forest)

        self.assertEqual(
            api.workspace.roots_for([Path("elsewhere/x.ltx")]),
            set(),
        )

    def test_real_forest_maps_include_to_its_root(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "configs"
            cfg.mkdir(parents=True)
            root = cfg / "system.ltx"
            included = cfg / "misc" / "a.ltx"
            included.parent.mkdir()
            root.write_text(
                '[system]\n#include "misc\\a.ltx"\n',
                encoding="cp1251",
            )
            included.write_text("[test]\nx = 1\n", encoding="cp1251")

            forest = TreeBuilder(cache=FileCacheSystem()).build(
                cfg,
                root,
                use_cache=False,
            )
            api = PlatformApi(
                FakeEvents(),
                FakeCommands(),
                None,
                None,
                None,
            )
            api.workspace.set_forest(forest)

            self.assertEqual(
                api.workspace.roots_for([included]),
                {root},
            )
            self.assertEqual(api.workspace.roots_for([root]), {root})


class ProjectEditInvalidationTest(unittest.TestCase):
    def _api(self, root, forest):
        paths = SimpleNamespace(
            projects_dir=Path(root) / "projects",
            app_dir=Path(root),
        )
        services = _Services()
        api = PlatformApi(FakeEvents(), FakeCommands(), None, services, paths)
        api.project.create("targeted", save_mode="root")
        api.workspace.set_forest(forest)
        return api, services

    def test_editing_child_invalidates_only_its_root(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "configs" / "system.ltx"
            child = Path(d) / "configs" / "misc" / "shared.ltx"
            forest = _Forest(
                [root],
                include_parents={child: {root}},
            )
            api, services = self._api(d, forest)

            api.on_document_changed(child, "[sec]\nx = 1\n")

            self.assertEqual(services.invalidated, [{root}])
            self.assertEqual(services.full_invalidations, 0)
            self.assertEqual(api._project_prebuild_roots, {root})

            api._start_project_prebuild()
            self.assertEqual(services.prebuild_starts, [{root}])

    def test_editing_root_invalidates_only_that_root(self):
        with tempfile.TemporaryDirectory() as d:
            root_a = Path(d) / "configs" / "a.ltx"
            root_b = Path(d) / "configs" / "b.ltx"
            forest = _Forest([root_a, root_b])
            api, services = self._api(d, forest)

            api.on_document_changed(root_a, "[sec]\nx = 1\n")

            self.assertEqual(services.invalidated, [{root_a}])
            self.assertEqual(services.full_invalidations, 0)

    def test_unknown_path_falls_back_to_full_invalidation(self):
        with tempfile.TemporaryDirectory() as d:
            forest = _Forest([Path(d) / "configs" / "system.ltx"])
            api, services = self._api(d, forest)

            api.on_document_changed(
                Path(d) / "elsewhere" / "unknown.ltx",
                "[sec]\nx = 1\n",
            )

            self.assertEqual(services.invalidated, [])
            self.assertEqual(services.full_invalidations, 1)
            self.assertIsNone(api._project_prebuild_roots)


class _CoreCache:
    def clear(self):
        pass


class _CoreXml:
    def clear(self):
        pass


class _CoreItems:
    def clear_memory(self):
        pass


class _CoreResolver:
    def __init__(self):
        self.invalidated_roots = []

    def invalidate_cache(self):
        pass

    def invalidate_roots(self, roots):
        self.invalidated_roots.append(set(roots))


class _FakePrebuild:
    """Записывает запуски так же, как настоящий PrebuildService."""

    def __init__(self):
        self.state = "done"
        self.starts = []

    def status(self):
        return {"state": self.state}

    def start(self, forest, config_dir, overlay=None, roots=None):
        self.starts.append((forest, config_dir, overlay, roots))
        self.state = "running"

    def wait(self, log=None, timeout=None):
        return None


class OverlayScopeTest(unittest.TestCase):
    """Overlay фильтруется по поддереву корня, а не применяется целиком."""

    def _build(self, root_dir):
        cfg = Path(root_dir) / "configs"
        (cfg / "items").mkdir(parents=True)
        a = cfg / "items" / "a.ltx"
        b = cfg / "items" / "b.ltx"
        system = cfg / "system.ltx"
        other = cfg / "other.ltx"
        a.write_text("[a]\nx = 1\n", encoding="cp1251")
        b.write_text("[b]\ny = 1\n", encoding="cp1251")
        system.write_text(
            '[system]\n#include "items\\a.ltx"\n',
            encoding="cp1251",
        )
        other.write_text(
            '[other]\n#include "items\\b.ltx"\n',
            encoding="cp1251",
        )
        forest = TreeBuilder(FileCacheSystem()).build(
            cfg,
            system,
            use_cache=False,
        )
        service = ResolverService(FileCacheSystem(), ResolvedCache())
        return cfg, forest, service, system, other, a, b

    def test_overlay_for_root_keeps_only_its_subtree(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, forest, service, system, other, a, b = self._build(d)
            overlay = ProjectOverlay(
                source_files={
                    a: "[a]\nx = 2\n",
                    b: "[b]\ny = 2\n",
                }
            )

            system_overlay = service.overlay_for_root(
                forest,
                system,
                overlay,
            )
            other_overlay = service.overlay_for_root(
                forest,
                other,
                overlay,
            )

            self.assertEqual(
                list(system_overlay.source_files),
                [a],
            )
            self.assertEqual(
                list(other_overlay.source_files),
                [b],
            )

    def test_overlay_for_unrelated_root_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, forest, service, system, other, a, b = self._build(d)
            overlay = ProjectOverlay(source_files={a: "[a]\nx = 2\n"})

            self.assertIsNone(
                service.overlay_for_root(forest, other, overlay)
            )

    def test_editing_draft_does_not_rekey_other_roots(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, forest, service, system, other, a, b = self._build(d)

            def key(root, overlay):
                filtered = service.overlay_for_root(
                    forest,
                    root,
                    overlay,
                )
                return service.resolved_key(
                    root,
                    forest,
                    cfg,
                    overlay=filtered,
                )

            base = ProjectOverlay()
            system_key = key(system, base)
            other_key = key(other, base)

            edit_a = ProjectOverlay(source_files={a: "[a]\nx = 2\n"})
            self.assertNotEqual(key(system, edit_a), system_key)
            self.assertEqual(key(other, edit_a), other_key)

            edit_b = ProjectOverlay(source_files={b: "[b]\ny = 2\n"})
            self.assertEqual(key(system, edit_b), system_key)
            self.assertNotEqual(key(other, edit_b), other_key)


class SectionGraphPrebuildFlowTest(unittest.TestCase):
    """«Связи секции» после правки не должны запускать полную пересборку."""

    def _services(self, forest, config_dir):
        services = CoreServices.__new__(CoreServices)
        services.cache = _CoreCache()
        services.resolver = _CoreResolver()
        services.xml = _CoreXml()
        services.items = _CoreItems()
        services.item_icons = _CoreItems()
        services.localization = _CoreItems()
        services.prebuild = _FakePrebuild()
        services._prebuild_forest = forest
        services._prebuild_config_dir = config_dir
        services._prebuild_overlay = {}
        services._prebuild_stale = False
        services._prebuild_roots = None
        return services

    def test_graph_flow_prebuilds_only_edited_root(self):
        with tempfile.TemporaryDirectory() as d:
            config_dir = Path(d) / "configs"
            root = config_dir / "system.ltx"
            other = config_dir / "other.ltx"
            child = config_dir / "misc" / "shared.ltx"
            forest = _Forest(
                [root, other],
                include_parents={child: {root}},
            )
            services = self._services(forest, config_dir)
            paths = SimpleNamespace(
                projects_dir=Path(d) / "projects",
                app_dir=Path(d),
            )
            api = PlatformApi(
                FakeEvents(),
                FakeCommands(),
                None,
                services,
                paths,
            )
            api.project.create("graph", save_mode="root")
            api.workspace.set_forest(forest)

            api.on_document_changed(child, "[misc]\nx = 1\n")

            # Ровно так граф связей просит предсборку перед поиском.
            self.assertTrue(
                services.ensure_prebuild(
                    forest,
                    config_dir,
                    overlay=api.project_overlay(),
                )
            )
            services.wait_prebuild(timeout=120)

            starts = services.prebuild.starts
            self.assertEqual(len(starts), 1)
            self.assertEqual(starts[-1][3], {root})
            self.assertEqual(services.resolver.invalidated_roots, [{root}])


if __name__ == "__main__":
    unittest.main()
