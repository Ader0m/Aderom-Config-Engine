# -*- coding: utf-8 -*-
"""Read-only границы SDK: лес, contribution, пути."""
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from tests.support import SRC  # noqa: F401

from sdk import AppPaths, ForestRef
from sdk.contracts import ProjectOverlay, SectionGraphContribution
from ui.platform.sdk_bridge import CoreApiAdapter, UiApiAdapter
from sdk.contracts.forest import forest_ref, unwrap_forest


class _Forest:
    def __init__(self):
        self.cache_id = "forest-1"
        self.scan_dir = "configs"
        self.system_root = "configs/system.ltx"
        self.total_files = 2
        self.total_xml_files = 1
        self.files = [Path("a.ltx"), Path("b.ltx")]
        self.xml_files = [Path("c.xml")]
        self.roots = [Path("a.ltx")]
        self.include_parents = {}


class ForestRefTest(unittest.TestCase):
    def test_ref_exposes_only_read_only_projections(self):
        raw = _Forest()
        ref = ForestRef(raw)

        self.assertEqual(ref.cache_id, "forest-1")
        self.assertEqual(ref.files, (Path("a.ltx"), Path("b.ltx")))
        self.assertEqual(ref.xml_files, (Path("c.xml"),))
        self.assertEqual(ref.roots, (Path("a.ltx"),))
        self.assertEqual(ref.total_files, 2)
        self.assertEqual(ref.total_xml_files, 1)
        self.assertIs(unwrap_forest(ref), raw)
        self.assertIs(forest_ref(ref), ref)

        with self.assertRaises(FrozenInstanceError):
            ref.files = ()

    def test_ref_is_live_but_never_returns_mutable_collections(self):
        raw = _Forest()
        ref = ForestRef(raw)

        raw.files.append(Path("new.ltx"))

        self.assertEqual(
            ref.files,
            (Path("a.ltx"), Path("b.ltx"), Path("new.ltx")),
        )
        self.assertIsInstance(ref.files, tuple)

    def test_core_adapter_wraps_tree_and_unwraps_for_provider(self):
        raw = _Forest()

        class Provider:
            def __init__(self):
                self.tree = self

            def build_tree(self, *args, **kwargs):
                return raw

            def relative_path(self, forest, path):
                self.seen = forest
                return "ok"

        provider = Provider()
        adapter = CoreApiAdapter(provider)
        ref = adapter.build_tree(Path("configs"), Path("system.ltx"))

        self.assertIsInstance(ref, ForestRef)
        self.assertEqual(adapter.relative_path(ref, Path("a.ltx")), "ok")
        self.assertIs(provider.seen, raw)


class ReadOnlyContributionTest(unittest.TestCase):
    def test_edge_styles_and_openers_are_frozen(self):
        contribution = SectionGraphContribution(
            edge_styles={"custom": object()},
            openers={"custom": lambda node: None},
        )

        with self.assertRaises(TypeError):
            contribution.edge_styles["other"] = object()
        with self.assertRaises(AttributeError):
            contribution.openers.clear()
        with self.assertRaises(FrozenInstanceError):
            contribution.edge_styles = {}


class ReadOnlyPathsTest(unittest.TestCase):
    def test_app_paths_are_frozen(self):
        paths = AppPaths(
            app_dir=Path("src"),
            generated_dir=Path("generated"),
            projects_dir=Path("projects"),
        )

        with self.assertRaises(FrozenInstanceError):
            paths.projects_dir = Path("other")


class ReadOnlyOverlayTest(unittest.TestCase):
    def test_resolved_edit_values_are_normalized(self):
        overlay = ProjectOverlay(
            resolved_edits={
                Path("root.ltx"): ("old", "new", [1, 2]),
            },
        )

        self.assertEqual(
            overlay.resolved_edits[Path("root.ltx")],
            ("old", "new", (1, 2)),
        )
        with self.assertRaises(TypeError):
            overlay.resolved_edits[Path("other.ltx")] = ("a", "b", ())


class UiForestBoundaryTest(unittest.TestCase):
    def test_workspace_forest_is_wrapped_and_publish_passes_ref(self):
        raw = _Forest()

        class Platform:
            def __init__(self):
                self.published = None

            def workspace_forest(self):
                return raw

            def publish_tree_ready(self, forest):
                self.published = forest
                return "ready"

        platform = Platform()
        ui = UiApiAdapter(platform)

        self.assertIsInstance(ui.workspace_forest(), ForestRef)
        ref = ForestRef(raw)
        self.assertEqual(ui.publish_tree_ready(ref), "ready")
        self.assertIs(platform.published, ref)
        self.assertIs(unwrap_forest(platform.published), raw)


if __name__ == "__main__":
    unittest.main()
