# -*- coding: utf-8 -*-
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.patching.xml.generator import DxmlGenerator
from core.patching.planner import PatchPlanner
from core.patching.xml.verifier import XmlModelPatchVerifier
from ui.project import ProjectService


BEFORE = """<root>
    <item id="a" width="10">old</item>
    <item id="b" width="20">keep</item>
</root>
"""

AFTER = """<root>
    <item id="a" width="12">new</item>
    <item id="b" width="20">keep</item>
    <item id="c" width="30">added</item>
</root>
"""


class Forest:
    def __init__(self, config_dir, xml_files):
        self.scan_dir = str(config_dir)
        self.files = []
        self.xml_files = list(xml_files)
        self.rel_paths = {
            path: str(path.relative_to(config_dir)).replace("/", "\\")
            for path in xml_files
        }
        self.include_parents = {}


class Services:
    def __init__(self):
        self.patch = PatchPlanner()

    def generate_patch(self, plan):
        generated = DxmlGenerator().generate(plan)
        verification = XmlModelPatchVerifier().verify(generated)
        return replace(generated, verification=verification)


class ForkServices(Services):
    def __init__(self):
        super().__init__()
        from core.patching.xml.fork import XmlForkService

        self.xml_fork = XmlForkService(self)

    def plan_xml_fork(self, *args, **kwargs):
        return self.xml_fork.plan(*args, **kwargs)


class ProjectXmlPatchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name) / "projects"
        self.config_dir = Path(self.temp.name) / "configs"
        (self.config_dir / "ui").mkdir(parents=True)
        self.source = self.config_dir / "ui" / "test.xml"
        self.source.write_bytes(BEFORE.encode("windows-1251"))
        self.forest = Forest(self.config_dir, [self.source])
        self.services = Services()
        self.project = ProjectService(self.base)
        self.project.create("proj")

    def test_project_uses_xml_fork_for_auto_mode(self):
        services = ForkServices()
        self.project.update_draft(self.source, AFTER)
        preview = self.project.build_preview(services, self.forest)
        self.assertEqual(len(preview.items), 1)
        item = preview.items[0]
        self.assertTrue(item.ok, item.error)
        self.assertEqual(item.plan.format, "xml_fork")
        names = [
            Path(file.relative_path).name
            for file in item.generated.files
        ]
        self.assertTrue(
            any(name.endswith("_ace_proj.xml") for name in names)
        )

    def test_script_reference_becomes_separate_preview_item(self):
        scripts = Path(self.temp.name) / "gamedata" / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        (scripts / "tool.script").write_bytes(
            b"local a = 1\nlocal b = 2\nlocal c = 3\n"
            b"local d = 4\nlocal e = 5\n"
            b"local key = 'old_key'\n"
            b"local f = 6\nlocal g = 7\nlocal h = 8\n"
            b"local i = 9\nlocal j = 10\nlocal k = 11\n",
        )
        source = self.config_dir / "ui" / "script_source.xml"
        source.write_bytes(
            b'<root><item id="old_key" v="1"/></root>'
        )
        self.forest.xml_files.append(source)
        self.forest.rel_paths[source] = r"ui\script_source.xml"
        self.project.update_draft(
            source,
            '<root><item id="old_key" v="2"/></root>',
        )
        services = ForkServices()
        preview = self.project.build_preview(services, self.forest)
        scripts_items = [
            item for item in preview.items if item.kind == "script"
        ]
        self.assertTrue(scripts_items)
        item = scripts_items[0]
        self.assertFalse(item.is_patch)
        self.assertIn("tool.script", item.title)
        self.assertIn("local key = 'old_key'", item.text)
        self.assertIn("local e = 5", item.text)
        self.assertIn("local f = 6", item.text)
        self.assertNotIn(item, preview.ok_items)

    def test_xml_override_full_copy(self):
        self.project.set_xml_patch_mode("xml_override")
        self.project.update_draft(self.source, AFTER)
        preview = self.project.build_preview(self.services, self.forest)
        self.assertEqual(len(preview.items), 1)
        item = preview.items[0]
        self.assertTrue(item.ok, item.error)
        self.assertEqual(item.plan.xml_mode, "full_copy")
        self.assertEqual(len(item.generated.files), 1)
        file = item.generated.files[0]
        self.assertEqual(file.relative_path.name, "test.xml")
        self.assertIn("<item", file.text)
        self.assertEqual(file.text, AFTER)

    def test_resolved_xml_override_full_copy(self):
        self.project.set_xml_patch_mode("xml_override")
        self.project.update_resolved_draft(
            "tab-override",
            self.source,
            "test.xml",
            BEFORE,
            AFTER,
            line_sources=(),
        )
        preview = self.project.build_preview(self.services, self.forest)
        self.assertEqual(len(preview.items), 1)
        item = preview.items[0]
        self.assertTrue(item.ok, item.error)
        self.assertEqual(item.plan.xml_mode, "full_copy")
        self.assertEqual(item.generated.files[0].text, AFTER)

    def test_raw_xml_preview(self):
        self.project.update_draft(self.source, AFTER)
        preview = self.project.build_preview(
            self.services,
            self.forest,
        )
        self.assertEqual(len(preview.items), 1)
        item = preview.items[0]
        self.assertTrue(item.ok, item.error)
        self.assertEqual(item.plan.xml_mode, "mixed")

    def test_resolved_xml_preview(self):
        self.project.update_resolved_draft(
            "tab-xml",
            self.source,
            "test.xml",
            BEFORE,
            AFTER,
            line_sources=(),
        )
        preview = self.project.build_preview(
            self.services,
            self.forest,
        )
        self.assertEqual(len(preview.items), 1)
        item = preview.items[0]
        self.assertTrue(item.ok, item.error)
        self.assertEqual(item.plan.xml_mode, "dxml_lua")

    def test_all_in_one_combines_xml_additions(self):
        second = self.config_dir / "ui" / "second.xml"
        second.write_bytes(
            """<root><item id="x" width="1">x</item></root>""".encode(
                "windows-1251"
            )
        )
        self.forest.xml_files.append(second)
        self.forest.rel_paths[second] = r"ui\second.xml"

        self.project.set_patch_mode("all_in_one")
        self.project.update_draft(
            self.source,
            """<root>
                <item id="a" width="10">old</item>
                <item id="b" width="20">keep</item>
                <item id="c" width="30">added</item>
            </root>
            """,
        )
        self.project.update_draft(
            second,
            """<root><item id="x" width="1">x</item><item id="y" width="2">y</item></root>""",
        )

        preview = self.project.build_preview(
            self.services,
            self.forest,
        )
        self.assertEqual(len(preview.items), 1)
        item = preview.items[0]
        self.assertTrue(item.ok, item.error)
        names = [
            Path(file.relative_path).name
            for file in item.generated.files
        ]
        additions = [
            name for name in names if name.endswith("_ace_proj.xml")
        ]
        self.assertEqual(len(additions), 2)
        self.assertIn("test_ace_proj.xml", additions)
        self.assertIn("second_ace_proj.xml", additions)
        combined = [
            file for file in item.generated.files
            if file.relative_path.name == "test_ace_proj.xml"
        ][0]
        self.assertIn('id="c"', combined.text)
        second_new = [
            file for file in item.generated.files
            if file.relative_path.name == "second_ace_proj.xml"
        ][0]
        self.assertIn('id="y"', second_new.text)


if __name__ == "__main__":
    unittest.main()
