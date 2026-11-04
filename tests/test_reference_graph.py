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


if __name__ == "__main__":
    unittest.main()
