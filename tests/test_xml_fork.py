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

    def test_root_mode_merges_references_from_different_ltx_files(self):
        from core.tools.cache import FileCacheSystem
        from core.tree.builder import TreeBuilder
        from tests.helpers import write_text

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "gamedata" / "configs"
            write_text(
                cfg / "system.ltx",
                '[system]\n'
                '#include "mod_system_z_mas_bas_reflex.ltx"\n'
                '#include "items/weapons/weapon_addons.ltx"\n',
            )
            write_text(
                cfg / "mod_system_z_mas_bas_reflex.ltx",
                "[0kp2]\n"
                "inv_name = old_key\n"
                "inv_name_short = old_key\n",
            )
            write_text(
                cfg / "items" / "weapons" / "weapon_addons.ltx",
                "[0kp2]\n"
                "inv_name = old_key\n"
                "inv_name_short = old_key\n",
            )
            xml_path = write_text(
                cfg / "text" / "rus" / "strings.xml",
                '<string_table>\n'
                '  <string id="old_key"><text>old</text></string>\n'
                "</string_table>\n",
            )
            after = (
                '<string_table>\n'
                '  <string id="old_key"><text>new</text></string>\n'
                "</string_table>\n"
            )
            forest = TreeBuilder(FileCacheSystem()).build(
                cfg,
                cfg / "system.ltx",
                use_cache=False,
            )

            result = XmlForkService(self.services).plan(
                xml_path,
                '<string_table>\n'
                '  <string id="old_key"><text>old</text></string>\n'
                "</string_table>\n",
                after,
                forest=forest,
                options=PatchOptions(
                    project_tag="proj",
                    patch_mod="ACE Patch - proj",
                ),
                project_mode="root",
            )

            ltx_files = [
                file for file in result.generated.files
                if file.relative_path.suffix.lower() == ".ltx"
            ]
            self.assertEqual(len(ltx_files), 1)
            self.assertEqual(
                ltx_files[0].text.count("old_key_ace_proj"),
                2,
            )
            self.assertNotIn(
                "изменён несколькими файлами",
                ltx_files[0].text,
            )
            self.assertIn(
                "Заменён ключ old_key в [0kp2].inv_name",
                ltx_files[0].text,
            )
            self.assertIn(
                "Правило Root объединило перепривязки из 2 файлов",
                ltx_files[0].text,
            )

    def test_path_aliases_do_not_generate_duplicate_patch_files(self):
        ltx_alias = (
            self.config / "items" / ".." / "items" / "items.ltx"
        )
        xml_alias = (
            self.config / "ui" / ".." / "ui" / "other.xml"
        )
        forest = Forest(
            self.config,
            [self.ltx, ltx_alias],
            [self.source, self.other_xml, xml_alias],
        )

        result = XmlForkService(self.services).plan(
            self.source,
            self.before,
            self.after,
            forest=forest,
            options=PatchOptions(
                project_tag="proj",
                patch_mod="ACE Patch - proj",
            ),
            project_mode="on_file",
        )

        self.assertTrue(result.generated.verification.ok)
        ltx_files = [
            file for file in result.generated.files
            if file.relative_path.suffix.lower() == ".ltx"
        ]
        self.assertEqual(len(ltx_files), 1)
        self.assertEqual(
            ltx_files[0].text.count("old_key_ace_proj"),
            2,
        )

        xml_copies = [
            file for file in result.generated.files
            if file.relative_path.name == "other.xml"
        ]
        self.assertEqual(len(xml_copies), 1)

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
            project_mode="on_file",
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
        self.assertEqual(document.root().tag, "root")
        button = document.find((("root", 0), ("button", 0)))
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
        # Локализация должна остаться валидной строковой таблицей.
        self.assertTrue(text.startswith("<?xml"))
        self.assertIn("<string_table>", text)
        self.assertIn("</string_table>", text)
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
        # Новый файл — самостоятельный XML: declaration + корень исходника.
        self.assertTrue(text.startswith("<?xml"))
        self.assertIn("<root>", text)
        self.assertIn("</root>", text)
        self.assertTrue(result.generated.verification.ok)

    def test_multiline_text_is_not_corrupted_by_wrapper(self):
        before = (
            '<string_table>\n'
            '\t<string id="st_key"><text>old</text></string>\n'
            "</string_table>\n"
        )
        after = (
            '<string_table>\n'
            '\t<string id="st_key"><text>line1\\n'
            "\n\t\talpha\\n"
            "\n\t\tomega</text></string>\n"
            "</string_table>\n"
        )
        result = XmlForkService(self.services).plan(
            self.source,
            before,
            after,
            forest=self.forest,
            options=PatchOptions(project_tag="proj"),
        )
        text = result.generated.files[0].text
        self.assertTrue(result.generated.verification.ok)
        self.assertIn("<string_table>", text)
        # Переводы строк и пробелы внутри <text> — часть значения.
        self.assertIn("\n\t\talpha\\n", text)
        self.assertIn("\n\t\tomega", text)
        self.assertNotIn("\n\t\t\talpha", text)

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

    def test_mod_patch_reference_is_rewired_via_on_file(self):
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
