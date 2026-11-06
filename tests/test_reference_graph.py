# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.models import LineSource
from core.reference_graph import ReferenceGraphBuilder


class FakeForest:
    def __init__(self, roots, files, xml_files, scan_dir):
        self.roots = roots
        self.files = files
        self.xml_files = xml_files
        self.scan_dir = str(scan_dir)
        self.display_children = {}
        self.rel_paths = {p: p.name for p in files + xml_files}


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

        lost_xml = [
            node for node in graph.lost_children
            if node.file_path == self.xml
        ]
        self.assertEqual(lost_xml, [])

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

        visual_node = by_type["visual_reference"]
        self.assertEqual(visual_node.display_name, "mili_maps.ogf")
        self.assertEqual(visual_node.target_path, model)
        self.assertIsNone(visual_node.physical_path)
        self.assertEqual(visual_node.first_match_line, 3)

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
            self.assertEqual(node.file_path, self.system)
            self.assertEqual(node.first_match_line, 1)
            self.assertEqual(node.related_file_path, parents_file)
            self.assertEqual(
                node.related_line,
                expected_lines[node.related_name],
            )
            self.assertFalse(node.related_resolved)

    def test_vfs_reports_lost_parent_link(self):
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

        lost_parents = [
            node for node in graph.lost_children
            if node.related_name
        ]
        self.assertEqual(
            [node.related_name for node in lost_parents],
            ["base_parent"],
        )
        lost = lost_parents[0]
        self.assertEqual(lost.related_file_path, parents_file)
        self.assertEqual(lost.related_line, 1)
        self.assertFalse(lost.related_resolved)

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
