# -*- coding: utf-8 -*-
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.patching.xml.document import parse_xml_document
from core.patching.xml.generator import DxmlGenerator
from core.patching.ltx.generator import DltxGenerator
from core.patching.models import PatchOptions, VerificationResult
from core.patching.planner import PatchPlanner
from core.patching.xml.fork import XmlForkService
from core.patching.xml.verifier import XmlModelPatchVerifier


class Services:
    def __init__(self):
        self.patch = PatchPlanner()

    def generate_patch(self, plan):
        if plan.is_xml:
            generated = DxmlGenerator().generate(plan)
            verification = XmlModelPatchVerifier().verify(generated)
        else:
            generated = DltxGenerator().generate(plan)
            verification = VerificationResult(ok=True)
        return replace(generated, verification=verification)

    def plan_xml_fork(self, *args, **kwargs):
        return XmlForkService(self).plan(*args, **kwargs)


class Forest:
    def __init__(self, config_dir, ltx_files, xml_files):
        self.scan_dir = str(config_dir)
        self.files = list(ltx_files)
        self.xml_files = list(xml_files)
        self.rel_paths = {
            path: str(path.relative_to(config_dir)).replace("/", "\\")
            for path in list(ltx_files) + list(xml_files)
        }
        self.include_parents = {}


class XmlForkTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.config = root / "gamedata" / "configs"
        (self.config / "ui").mkdir(parents=True)
        (self.config / "items").mkdir(parents=True)
        (root / "gamedata" / "scripts").mkdir(parents=True)

        self.source = self.config / "ui" / "source.xml"
        self.before = (
            '<root><button id="old_key" width="10">old</button></root>'
        )
        self.after = (
            '<root><button id="old_key" width="12">new</button></root>'
        )
        self.source.write_bytes(self.before.encode("windows-1251"))

        self.other_xml = self.config / "ui" / "other.xml"
        self.other_before = (
            '<root><ref id="r" target="old_key"/></root>'
        )
        self.other_xml.write_bytes(self.other_before.encode("windows-1251"))

        self.ltx = self.config / "items" / "items.ltx"
        self.ltx.write_bytes(
            b"[item]\nui = old_key\nlist = a, old_key, b\n",
        )

        (root / "gamedata" / "scripts" / "test.script").write_bytes(
            b"local key = 'old_key'\n",
        )

        self.forest = Forest(
            self.config,
            [self.ltx],
            [self.source, self.other_xml],
        )
        self.services = Services()

    def test_fork_creates_new_xml_rewires_ltx_and_xml(self):
        result = XmlForkService(self.services).plan(
            self.source,
            self.before,
            self.after,
            forest=self.forest,
            options=PatchOptions(
                project_tag="proj",
                patch_mod="ACE Patch - proj",
            ),
            project_mode="in_place",
        )
        self.assertTrue(result.keys)
        self.assertEqual(result.keys[0].old_key, "old_key")
        self.assertEqual(result.keys[0].new_key, "old_key_ace_proj")
        self.assertTrue(result.generated.verification.ok)

        names = [
            Path(file.relative_path).name
            for file in result.generated.files
        ]
        self.assertTrue(any(name.endswith("source_ace_proj.xml") for name in names))
        self.assertTrue(any(name.endswith(".ltx") for name in names))
        self.assertTrue(any(name == "other.xml" for name in names))
        self.assertFalse(any(name.startswith("modxml_") for name in names))
        self.assertFalse(
            any(
                file.relative_path.suffix.lower() == ".script"
                for file in result.generated.files
            )
        )

        new_file = next(
            file for file in result.generated.files
            if file.relative_path.name == "source_ace_proj.xml"
        )
        document = parse_xml_document(new_file.text)
        button = document.find((("button", 0),))
        self.assertIsNotNone(button)
        self.assertEqual(button.attrs["id"], "old_key_ace_proj")
        self.assertEqual(button.attrs["width"], "12")
        self.assertEqual(button.text(), "new")

        ltx_file = next(
            file for file in result.generated.files
            if file.relative_path.suffix == ".ltx"
        )
        self.assertIn("old_key_ace_proj", ltx_file.text)
        self.assertNotIn("old_key", ltx_file.text.replace(
            "old_key_ace_proj", ""
        ))

        other_file = next(
            file for file in result.generated.files
            if file.relative_path.name == "other.xml"
        )
        self.assertIn("old_key_ace_proj", other_file.text)
        self.assertNotIn("modxml_", other_file.text)

        script_warnings = [
            issue for issue in result.plan.warnings
            if issue.code == "xml_key_in_script"
        ]
        self.assertTrue(script_warnings)

    def test_localization_edit_changes_only_same_language(self):
        rus = self.config / "text" / "rus" / "ui_st.xml"
        eng = self.config / "text" / "eng" / "ui_st.xml"
        rus.parent.mkdir(parents=True, exist_ok=True)
        eng.parent.mkdir(parents=True, exist_ok=True)
        rus.write_bytes(
            '<string_table><string id="ui_key">старое</string></string_table>'.encode(
                "windows-1251"
            )
        )
        eng.write_bytes(
            '<string_table><string id="ui_key">old</string></string_table>'.encode(
                "windows-1251"
            )
        )
        forest = Forest(self.config, [self.ltx], [rus, eng])
        before = '<string_table><string id="ui_key">старое</string></string_table>'
        after = '<string_table><string id="ui_key">новое</string></string_table>'

        result = XmlForkService(self.services).plan(
            rus,
            before,
            after,
            forest=forest,
            options=PatchOptions(project_tag="proj"),
        )

        self.assertTrue(result.keys)
        self.assertEqual(result.keys[0].new_key, "ui_key_ace_proj")
        self.assertEqual(len(result.generated.files), 1)
        path = result.generated.files[0].relative_path
        self.assertEqual(path.name, "ui_st_ace_proj.xml")
        self.assertIn("rus", [part.lower() for part in path.parts])
        self.assertNotIn("eng", [part.lower() for part in path.parts])
        text = result.generated.files[0].text
        self.assertIn('<string id="ui_key_ace_proj">', text)
        self.assertIn("новое", text)
        self.assertNotIn('id="ui_key"', text)
        self.assertIn("locale", result.plan.warnings[0].code)

    def test_script_reference_found_outside_gamedata_scripts(self):
        root = Path(self.temp.name)
        (root / "scripts").mkdir(exist_ok=True)
        (root / "scripts" / "alt.script").write_bytes(
            b"local key = 'old_key'\n",
        )
        result = XmlForkService(self.services).plan(
            self.source,
            self.before,
            self.after,
            forest=self.forest,
            options=PatchOptions(project_tag="proj"),
        )
        messages = [
            issue.message for issue in result.plan.warnings
            if issue.code == "xml_key_in_script"
        ]
        self.assertTrue(messages)
        self.assertTrue(any("alt.script" in message for message in messages))

    def test_unkeyed_block_gets_generated_key(self):
        before = "<root><label>old</label></root>"
        after = "<root><label>new</label></root>"
        result = XmlForkService(self.services).plan(
            self.source,
            before,
            after,
            forest=self.forest,
            options=PatchOptions(project_tag="proj"),
        )
        self.assertTrue(result.keys)
        self.assertIsNone(result.keys[0].old_key)
        self.assertEqual(result.keys[0].new_key, "ace_proj")
        text = result.generated.files[0].text
        self.assertIn('<label id="ace_proj">new</label>', text)
        self.assertNotIn("<root>", text)

    def test_explicit_new_key_is_used(self):
        after = (
            '<root><button id="new_key" width="12">new</button></root>'
        )
        result = XmlForkService(self.services).plan(
            self.source,
            self.before,
            after,
            forest=self.forest,
            options=PatchOptions(project_tag="proj"),
        )
        self.assertEqual(result.keys[0].new_key, "new_key")
        self.assertTrue(result.generated.verification.ok)

    def test_dxml_setting_is_used_for_xml_references(self):
        result = XmlForkService(self.services).plan(
            self.source,
            self.before,
            self.after,
            forest=self.forest,
            options=PatchOptions(
                project_tag="proj",
                xml_patch_mode="dxml_lua",
            ),
        )
        names = [
            Path(file.relative_path).name
            for file in result.generated.files
        ]
        self.assertTrue(any(name.startswith("modxml_") for name in names))
        self.assertFalse(any(name == "other.xml" for name in names))

    def test_mod_patch_reference_is_rewired_via_in_place(self):
        mod_patch = self.config / "items" / "mod_items_old.ltx"
        mod_patch.write_bytes(b"[item]\nui = old_key\n")
        self.forest.files.append(mod_patch)
        self.forest.rel_paths[mod_patch] = r"items\mod_items_old.ltx"
        result = XmlForkService(self.services).plan(
            self.source,
            self.before,
            self.after,
            forest=self.forest,
            options=PatchOptions(project_tag="proj"),
        )
        names = [
            Path(file.relative_path).name
            for file in result.generated.files
        ]
        self.assertTrue(
            any(
                name.startswith("mod_items_")
                and name.endswith("_proj.ltx")
                for name in names
            ),
            names,
        )
        self.assertFalse(
            any(name.startswith("mod_mod_") for name in names)
        )

    def test_override_setting_copies_reference_files(self):
        result = XmlForkService(self.services).plan(
            self.source,
            self.before,
            self.after,
            forest=self.forest,
            options=PatchOptions(
                project_tag="proj",
                xml_patch_mode="xml_override",
            ),
            project_mode="override",
        )
        names = [
            Path(file.relative_path).name
            for file in result.generated.files
        ]
        self.assertIn("other.xml", names)
        self.assertIn("items.ltx", names)
        self.assertFalse(any(name.startswith("modxml_") for name in names))


if __name__ == "__main__":
    unittest.main()
