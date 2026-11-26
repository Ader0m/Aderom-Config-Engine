# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import SRC  # noqa: F401

from core.cache.service import CacheService
from core.models import LineSource
from core.services.cache_control import CacheControl
from core.services.graph import GraphServices
from core.tools.cache import project_overlay_signature
from core.tree.graph import (
    EdgeType,
    GraphNode,
    MatchInfo,
    ReferenceGraphBuilder,
    _EDGE_CONFIDENCE,
)
from sdk.contracts import ProjectOverlay


class FakeForest:
    def __init__(
        self,
        roots,
        files,
        xml_files,
        scan_dir,
        cache_id="snapshot-1",
    ):
        self.roots = roots
        self.files = files
        self.xml_files = xml_files
        self.scan_dir = str(scan_dir)
        self.display_children = {}
        self.rel_paths = {p: p.name for p in files + xml_files}
        self.cache_id = cache_id


class ReferenceGraphTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.system = self.root / "system.ltx"
        self.xml = self.root / "menu.xml"
        self.system.write_text("", encoding="utf-8")
        self.xml.write_text("", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_resolved_graph_includes_xml_references(self):
        forest = FakeForest(
            roots=[self.system],
            files=[self.system],
            xml_files=[self.xml],
            scan_dir=self.root,
        )

        ltx_text = "[test]\nx=1\n[other]\nparent=test\n"
        ltx_sources = [
            LineSource(kind="original") for _ in ltx_text.splitlines()
        ]

        xml_text = "<w>\n  <text>test</text>\n</w>\n"
        xml_sources = [
            LineSource(kind="original") for _ in xml_text.splitlines()
        ]

        graph = ReferenceGraphBuilder().build_for_section(
            forest,
            "test",
            resolved_provider=lambda _p: (ltx_text, ltx_sources),
            xml_provider=lambda _p: (xml_text, xml_sources),
        )

        self.assertTrue(graph.root.resolved)
        self.assertEqual(graph.root.file_path, self.system)

        xml_nodes = [
            node for node in graph.children
            if node.file_path == self.xml
        ]
        self.assertEqual(len(xml_nodes), 1)
        self.assertTrue(xml_nodes[0].resolved)
        self.assertEqual(xml_nodes[0].first_match_line, 2)
        self.assertEqual(
            xml_nodes[0].matches[0].edge_type.value,
            "xml_reference",
        )
        self.assertEqual(xml_nodes[0].matches[0].confidence, "probable")

    def test_edge_confidence_rules(self):
        cases = {
            EdgeType.DECLARATION: "exact",
            EdgeType.INHERITANCE: "exact",
            EdgeType.TEXTURE_REFERENCE: "exact",
            EdgeType.VISUAL_REFERENCE: "exact",
            EdgeType.LTX_REFERENCE: "probable",
            EdgeType.XML_REFERENCE: "probable",
            EdgeType.SCRIPT_REFERENCE: "heuristic",
        }
        for edge_type, expected in cases.items():
            with self.subTest(edge_type=edge_type):
                self.assertEqual(
                    MatchInfo(edge_type, 1, "").confidence,
                    expected,
                )

    def test_edge_confidence_table_covers_all_types(self):
        self.assertEqual(set(EdgeType), set(_EDGE_CONFIDENCE))

    def test_graph_node_primary_confidence(self):
        exact = GraphNode(
            display_name="test",
            file_path=None,
            first_match_line=1,
            matches=[MatchInfo(EdgeType.DECLARATION, 1, "")],
        )
        probable = GraphNode(
            display_name="test",
            file_path=None,
            first_match_line=2,
            matches=[MatchInfo(EdgeType.LTX_REFERENCE, 2, "")],
        )

        self.assertEqual(exact.primary_confidence, "exact")
        self.assertEqual(probable.primary_confidence, "probable")

    def test_script_reference_is_heuristic(self):
        self.system.write_text("[test]\nx=1\n", encoding="utf-8")
        script = self.root / "test.script"
        script.write_text("local value = test\n", encoding="utf-8")
        forest = FakeForest(
            roots=[self.system],
            files=[self.system],
            xml_files=[],
            scan_dir=self.root,
        )

        graph = ReferenceGraphBuilder().build_for_section(
            forest,
            "test",
            vfs_mode=True,
        )

        script_matches = [
            match
            for node in graph.children
            if node.file_path == script
            for match in node.matches
            if match.edge_type.value == "script_reference"
        ]
        self.assertEqual(len(script_matches), 1)
        self.assertEqual(script_matches[0].confidence, "heuristic")

    def test_graph_result_is_cached_by_snapshot(self):
        forest = FakeForest(
            roots=[self.system],
            files=[self.system],
            xml_files=[],
            scan_dir=self.root,
        )
        cache = CacheService()
        builder = ReferenceGraphBuilder(cache_service=cache)
        calls = []
        text = "[test]\nx=1\n"
        sources = [LineSource(kind="original") for _ in text.splitlines()]

        def provider(_path):
            calls.append(1)
            return text, sources

        first = builder.build_for_section(
            forest,
            "test",
            resolved_provider=provider,
        )
        second = builder.build_for_section(
            forest,
            "test",
            resolved_provider=provider,
        )

        self.assertIsNot(first, second)
        self.assertEqual(first.section_name, second.section_name)
        self.assertEqual(len(first.children), len(second.children))
        self.assertEqual(calls, [1])
        stats = cache.stats()["namespaces"]["graph.section"]
        self.assertEqual(stats["computes"], 1)
        self.assertEqual(stats["hits"], 1)

        first.root.display_name = "mutated"
        third = builder.build_for_section(
            forest,
            "test",
            resolved_provider=provider,
        )
        self.assertNotEqual(third.root.display_name, "mutated")

    def test_graph_result_cache_is_invalidated_by_snapshot(self):
        first_forest = FakeForest(
            roots=[self.system],
            files=[self.system],
            xml_files=[],
            scan_dir=self.root,
            cache_id="snapshot-1",
        )
        second_forest = FakeForest(
            roots=[self.system],
            files=[self.system],
            xml_files=[],
            scan_dir=self.root,
            cache_id="snapshot-2",
        )
        builder = ReferenceGraphBuilder(cache_service=CacheService())
        calls = []
        text = "[test]\nx=1\n"
        sources = [LineSource(kind="original") for _ in text.splitlines()]

        def provider(_path):
            calls.append(1)
            return text, sources

        first = builder.build_for_section(
            first_forest,
            "test",
            resolved_provider=provider,
        )
        second = builder.build_for_section(
            second_forest,
            "test",
            resolved_provider=provider,
        )

        self.assertIsNot(first, second)
        self.assertEqual(calls, [1, 1])

    def test_fallback_graph_is_not_cached(self):
        self.system.write_text("[test]\nx=1\n", encoding="utf-8")
        forest = FakeForest(
            roots=[self.system],
            files=[self.system],
            xml_files=[],
            scan_dir=self.root,
        )
        builder = ReferenceGraphBuilder(cache_service=CacheService())
        calls = []
        text = "[test]\nx=1\n"
        sources = [LineSource(kind="original") for _ in text.splitlines()]

        def failing(_path):
            calls.append("fail")
            raise RuntimeError("resolver unavailable")

        first = builder.build_for_section(
            forest,
            "test",
            resolved_provider=failing,
        )

        def working(_path):
            calls.append("ok")
            return text, sources

        second = builder.build_for_section(
            forest,
            "test",
            resolved_provider=working,
        )

        self.assertFalse(first.root.resolved)
        self.assertTrue(second.root.resolved)
        self.assertIsNot(first, second)
        self.assertEqual(calls, ["fail", "ok"])

    def test_partial_resolved_graph_is_not_cached(self):
        extra = self.root / "extra.ltx"
        self.system.write_text("[test]\nx=1\n", encoding="utf-8")
        extra.write_text("[test]\ny=2\n", encoding="utf-8")
        forest = FakeForest(
            roots=[self.system, extra],
            files=[self.system, extra],
            xml_files=[],
            scan_dir=self.root,
        )
        builder = ReferenceGraphBuilder(cache_service=CacheService())
        calls = []
        text = "[test]\nx=1\n"
        sources = [LineSource(kind="original") for _ in text.splitlines()]

        def partial(path):
            calls.append(path)
            if path == extra:
                raise RuntimeError("root unavailable")
            return text, sources

        first = builder.build_for_section(
            forest,
            "test",
            resolved_provider=partial,
        )

        def working(path):
            calls.append(path)
            return text, sources

        second = builder.build_for_section(
            forest,
            "test",
            resolved_provider=working,
        )

        self.assertTrue(first.root.resolved)
        self.assertTrue(second.root.resolved)
        self.assertIsNot(first, second)
        self.assertEqual(len(calls), 4)

    def test_file_cache_tracks_file_stamp(self):
        builder = ReferenceGraphBuilder(cache_service=CacheService())
        self.system.write_text("[first]\n", encoding="utf-8")
        first = builder._read_file(self.system)

        self.system.write_text("[second]\n", encoding="utf-8")
        second = builder._read_file(self.system)

        self.assertEqual(first.splitlines(), ["[first]"])
        self.assertEqual(second.splitlines(), ["[second]"])

    def test_read_error_is_not_cached(self):
        builder = ReferenceGraphBuilder(cache_service=CacheService())
        self.system.write_text("", encoding="utf-8")

        with patch(
            "core.tree.graph.read_text_safe",
            side_effect=[OSError("busy"), "[ok]\n"],
        ):
            first = builder._read_file(self.system)
            second = builder._read_file(self.system)

        self.assertEqual(first, "")
        self.assertEqual(second.splitlines(), ["[ok]"])

    def test_clear_cache_forces_graph_rebuild(self):
        forest = FakeForest(
            roots=[self.system],
            files=[self.system],
            xml_files=[],
            scan_dir=self.root,
        )
        builder = ReferenceGraphBuilder(cache_service=CacheService())
        calls = []
        text = "[test]\nx=1\n"
        sources = [LineSource(kind="original") for _ in text.splitlines()]

        def provider(_path):
            calls.append(1)
            return text, sources

        builder.build_for_section(
            forest,
            "test",
            resolved_provider=provider,
        )
        builder.clear_cache()
        builder.build_for_section(
            forest,
            "test",
            resolved_provider=provider,
        )

        self.assertEqual(calls, [1, 1])

    def test_invalidate_paths_clears_graph_cache(self):
        forest = FakeForest(
            roots=[self.system],
            files=[self.system],
            xml_files=[],
            scan_dir=self.root,
        )
        builder = ReferenceGraphBuilder(cache_service=CacheService())
        calls = []
        text = "[test]\nx=1\n"
        sources = [LineSource(kind="original") for _ in text.splitlines()]

        def provider(_path):
            calls.append(1)
            return text, sources

        builder.build_for_section(
            forest,
            "test",
            resolved_provider=provider,
        )

        graph = GraphServices(
            builder,
            SimpleNamespace(invalidate_roots=lambda _roots: None),
            SimpleNamespace(),
        )
        control = CacheControl(
            cache_service=None,
            resolve=SimpleNamespace(
                invalidate_cache=lambda: None,
                invalidate_roots=lambda _roots: None,
            ),
            graph=graph,
            data=SimpleNamespace(clear_memory=lambda: None),
            prebuild=SimpleNamespace(mark_stale=lambda roots=None: None),
        )
        control.invalidate_paths([self.system])

        builder.build_for_section(
            forest,
            "test",
            resolved_provider=provider,
        )

        self.assertEqual(calls, [1, 1])

    def test_facade_graph_snapshot_includes_overlay(self):
        class RecordingBuilder:
            def __init__(self):
                self.kwargs = None

            def build_for_section(self, *args, **kwargs):
                self.kwargs = kwargs
                return object()

        builder = RecordingBuilder()
        graph = GraphServices(
            builder,
            SimpleNamespace(),
            SimpleNamespace(),
        )
        forest = SimpleNamespace(
            scan_dir=str(self.root),
            cache_id="snapshot-1",
        )
        overlay = ProjectOverlay(
            source_files={self.xml: "<root/>"},
        )

        graph.build_section_graph(
            forest,
            "test",
            overlay=overlay,
        )

        self.assertEqual(
            builder.kwargs["snapshot_id"],
            "snapshot-1|" + project_overlay_signature(overlay),
        )

    def test_xml_not_reported_as_lost_when_found_in_raw_graph(self):
        self.system.write_text(
            "[test]\nx=1\n[other]\nparent=test\n",
            encoding="utf-8",
        )
        self.xml.write_text(
            "<root>\n  <item>test</item>\n</root>\n",
            encoding="utf-8",
        )

        forest = FakeForest(
            roots=[self.system],
            files=[self.system],
            xml_files=[self.xml],
            scan_dir=self.root,
        )

        # VFS-режим сравнивает сырое дерево с сырым: XML, который уже
        # есть в итоговом дереве, не должен попадать в потерянные.
        graph = ReferenceGraphBuilder().build_for_section(
            forest,
            "test",
            vfs_mode=True,
        )

        found_xml = [
            node for node in graph.children
            if node.file_path == self.xml
        ]
        self.assertTrue(
            found_xml,
            "XML-файл должен быть найден в VFS-графе, иначе проверка "
            "потерянных связей ничего не доказывает",
        )
        self.assertTrue(
            any(
                match.edge_type.value == "xml_reference"
                for node in found_xml
                for match in node.matches
            )
        )

    def test_vfs_mode_does_not_call_resolved_providers(self):
        self.system.write_text(
            "[test]\nx=1\n[other]\nparent=test\n",
            encoding="utf-8",
        )
        self.xml.write_text(
            "<root>\n  <item>test</item>\n</root>\n",
            encoding="utf-8",
        )

        forest = FakeForest(
            roots=[self.system],
            files=[self.system],
            xml_files=[self.xml],
            scan_dir=self.root,
        )

        calls = []

        def resolved_provider(_path):
            calls.append("ltx")
            raise AssertionError(
                "VFS-режим не должен вызывать LTX-резолвер"
            )

        def xml_provider(_path):
            calls.append("xml")
            raise AssertionError(
                "VFS-режим не должен вызывать XML-резолвер"
            )

        ReferenceGraphBuilder().build_for_section(
            forest,
            "test",
            vfs_mode=True,
            resolved_provider=resolved_provider,
            xml_provider=xml_provider,
        )

        self.assertEqual(calls, [])

    def test_xml_only_forest_without_ltx_roots(self):
        forest = FakeForest(
            roots=[],
            files=[],
            xml_files=[self.xml],
            scan_dir=self.root,
        )
        xml_text = "<w>\n  <text>only_xml</text>\n</w>\n"
        xml_sources = [
            LineSource(kind="original") for _ in xml_text.splitlines()
        ]

        graph = ReferenceGraphBuilder().build_for_section(
            forest,
            "only_xml",
            resolved_provider=lambda _p: None,
            xml_provider=lambda _p: (xml_text, xml_sources),
        )

        self.assertEqual(graph.root.file_path, self.xml)
        self.assertEqual(graph.root.first_match_line, 2)

    def test_xml_only_section_becomes_root(self):
        forest = FakeForest(
            roots=[self.system],
            files=[self.system],
            xml_files=[self.xml],
            scan_dir=self.root,
        )

        ltx_text = "[other]\nx=1\n"
        ltx_sources = [
            LineSource(kind="original") for _ in ltx_text.splitlines()
        ]
        xml_text = "<w>\n  <text>only_xml</text>\n</w>\n"
        xml_sources = [
            LineSource(kind="original") for _ in xml_text.splitlines()
        ]

        graph = ReferenceGraphBuilder().build_for_section(
            forest,
            "only_xml",
            resolved_provider=lambda _p: (ltx_text, ltx_sources),
            xml_provider=lambda _p: (xml_text, xml_sources),
        )

        self.assertTrue(graph.root.resolved)
        self.assertEqual(graph.root.file_path, self.xml)
        self.assertEqual(graph.root.first_match_line, 2)

    def test_texture_and_visual_assets_are_resolved(self):
        game = self.root / "gamedata"
        configs = game / "configs"
        configs.mkdir(parents=True)

        system = configs / "system.ltx"
        system.write_text(
            "[test]\n"
            "icons_texture = ui\\ui_icon\n"
            "visual = dynamics\\equipments\\trade\\mili_maps.ogf\n"
            "[other]\n"
            "parent = test\n",
            encoding="utf-8",
        )

        texture = game / "textures" / "ui" / "ui_icon.dds"
        texture.parent.mkdir(parents=True)
        texture.write_bytes(b"DDS ")

        model = (
            game / "meshes" / "dynamics" / "equipments" / "trade"
            / "mili_maps.ogf"
        )
        model.parent.mkdir(parents=True)
        model.write_bytes(b"OGF ")

        forest = FakeForest(
            roots=[system],
            files=[system],
            xml_files=[],
            scan_dir=configs,
        )

        graph = ReferenceGraphBuilder().build_for_section(forest, "test")
        by_type = {
            node.matches[0].edge_type.value: node
            for node in graph.children
            if node.matches
        }

        tex_node = by_type["texture_reference"]
        self.assertEqual(tex_node.display_name, "ui_icon.dds")
        self.assertEqual(tex_node.target_path, texture)
        self.assertEqual(tex_node.physical_path, texture)
        self.assertEqual(tex_node.first_match_line, 2)
        self.assertEqual(tex_node.matches[0].confidence, "exact")

        visual_node = by_type["visual_reference"]
        self.assertEqual(visual_node.display_name, "mili_maps.ogf")
        self.assertEqual(visual_node.target_path, model)
        self.assertIsNone(visual_node.physical_path)
        self.assertEqual(visual_node.first_match_line, 3)
        self.assertEqual(visual_node.matches[0].confidence, "exact")

    def test_asset_without_disk_file_keeps_reference(self):
        self.system.write_text(
            "[test]\nvisual = no\\such\\model\n",
            encoding="utf-8",
        )
        forest = FakeForest(
            roots=[self.system],
            files=[self.system],
            xml_files=[],
            scan_dir=self.root,
        )

        graph = ReferenceGraphBuilder().build_for_section(forest, "test")
        visual_nodes = [
            node for node in graph.children
            if node.matches
            and node.matches[0].edge_type.value == "visual_reference"
        ]

        self.assertEqual(len(visual_nodes), 1)
        self.assertIsNone(visual_nodes[0].target_path)
        self.assertEqual(visual_nodes[0].raw_ref, "no\\such\\model")

    def test_asset_line_is_not_duplicated_as_ltx_reference(self):
        text = (
            "[mili_maps]\n"
            "visual = dynamics\\trade\\mili_maps.ogf\n"
        )
        self.system.write_text(text, encoding="utf-8")
        sources = [
            LineSource(kind="original") for _ in text.splitlines()
        ]
        forest = FakeForest(
            roots=[self.system],
            files=[self.system],
            xml_files=[],
            scan_dir=self.root,
        )

        graph = ReferenceGraphBuilder().build_for_section(
            forest,
            "mili_maps",
            resolved_provider=lambda _p: (text, sources),
        )

        edge_types = [
            match.edge_type.value
            for node in graph.children
            for match in node.matches
        ]
        self.assertIn("visual_reference", edge_types)
        self.assertNotIn("ltx_reference", edge_types)

    def test_section_parents_are_split_into_separate_links(self):
        self.system.write_text(
            "[test]:base_a, base_b\nx=1\n",
            encoding="utf-8",
        )
        parents_file = self.root / "parents.ltx"
        parents_file.write_text(
            "[base_a]\nx=1\n[base_b]\nx=1\n",
            encoding="utf-8",
        )
        forest = FakeForest(
            roots=[self.system, parents_file],
            files=[self.system, parents_file],
            xml_files=[],
            scan_dir=self.root,
        )

        graph = ReferenceGraphBuilder().build_for_section(forest, "test")

        parents = [
            node for node in graph.children
            if node.related_name
        ]
        self.assertEqual(
            sorted(node.related_name for node in parents),
            ["base_a", "base_b"],
        )
        expected_lines = {"base_a": 1, "base_b": 3}
        for node in parents:
            self.assertEqual(
                node.matches[0].edge_type.value,
                "inheritance",
            )
            self.assertEqual(node.matches[0].confidence, "exact")
            self.assertEqual(node.file_path, self.system)
            self.assertEqual(node.first_match_line, 1)
            self.assertEqual(node.related_file_path, parents_file)
            self.assertEqual(
                node.related_line,
                expected_lines[node.related_name],
            )
            self.assertFalse(node.related_resolved)

    def test_vfs_returns_parent_link_in_single_list(self):
        self.system.write_text("[test]\nx=1\n", encoding="utf-8")
        other = self.root / "other.ltx"
        other.write_text("[test]:base_parent\n", encoding="utf-8")
        parents_file = self.root / "parents.ltx"
        parents_file.write_text("[base_parent]\nx=1\n", encoding="utf-8")

        forest = FakeForest(
            roots=[self.system],
            files=[self.system],
            xml_files=[],
            scan_dir=self.root,
        )

        graph = ReferenceGraphBuilder().build_for_section(
            forest,
            "test",
            vfs_mode=True,
        )

        parent_nodes = [
            node for node in graph.children
            if node.related_name
        ]
        self.assertEqual(
            [node.related_name for node in parent_nodes],
            ["base_parent"],
        )
        found = parent_nodes[0]
        self.assertEqual(found.related_file_path, parents_file)
        self.assertEqual(found.related_line, 1)
        self.assertFalse(found.related_resolved)
        self.assertEqual(graph.lost_children, [])

    def test_resolved_graph_shows_section_parents(self):
        text = "[test]:base_a,base_b\nx=1\n"
        sources = [
            LineSource(kind="original") for _ in text.splitlines()
        ]
        parents_file = self.root / "parents.ltx"
        parents_text = "[base_a]\nx=1\n[base_b]\nx=1\n"
        parents_sources = [
            LineSource(kind="original")
            for _ in parents_text.splitlines()
        ]
        forest = FakeForest(
            roots=[self.system, parents_file],
            files=[self.system, parents_file],
            xml_files=[],
            scan_dir=self.root,
        )
        texts = {
            self.system: (text, sources),
            parents_file: (parents_text, parents_sources),
        }

        graph = ReferenceGraphBuilder().build_for_section(
            forest,
            "test",
            resolved_provider=lambda path: texts.get(path),
        )

        parents = [
            node for node in graph.children
            if node.related_name
        ]
        self.assertEqual(
            sorted(node.related_name for node in parents),
            ["base_a", "base_b"],
        )
        expected_lines = {"base_a": 1, "base_b": 3}
        for node in parents:
            self.assertEqual(node.related_file_path, parents_file)
            self.assertEqual(
                node.related_line,
                expected_lines[node.related_name],
            )
            self.assertTrue(node.related_resolved)


if __name__ == "__main__":
    unittest.main()
