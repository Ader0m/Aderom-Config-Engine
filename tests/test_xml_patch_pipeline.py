# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.patching.xml.generator import DxmlGenerator
from core.patching.models import (
    GeneratedPatch,
    PatchOptions,
    VerificationResult,
)
from core.patching.planner import PatchPlanner
from core.patching.writer import PatchWriteError, PatchWriter
from core.patching.xml.verifier import (
    XmlModelPatchVerifier,
    XmlResolverPatchVerifier,
)
from core.xml.service import XmlResolveService


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

ONLY_ADD = """<root>
    <item id="a" width="10">old</item>
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


class XmlPatchPipelineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config_dir = Path(self.temp.name) / "configs"
        (self.config_dir / "ui").mkdir(parents=True)
        self.source = self.config_dir / "ui" / "test.xml"
        self.source.write_bytes(BEFORE.encode("windows-1251"))
        self.forest = Forest(self.config_dir, [self.source])
        self.planner = PatchPlanner()
        self.generator = DxmlGenerator()
        self.verifier = XmlModelPatchVerifier()

    def _plan(self, text, resolved=False, options=None):
        options = options or PatchOptions(
            project_tag="proj",
            allow_delete_xml_element=True,
        )
        if resolved:
            return self.planner.plan_xml_resolved_edit(
                self.source,
                BEFORE,
                text,
                forest=self.forest,
                options=options,
            )
        return self.planner.plan_xml_document_change(
            self.source,
            BEFORE,
            text,
            forest=self.forest,
            options=options,
        )

    def test_only_additions_create_new_xml(self):
        plan = self._plan(ONLY_ADD)
        self.assertEqual(plan.xml_mode, "new_file")
        generated = self.generator.generate(plan)
        self.assertEqual(len(generated.files), 1)
        self.assertEqual(
            generated.files[0].relative_path.name,
            "test_ace_proj.xml",
        )
        self.assertIn('<item id="c"', generated.text)
        self.assertTrue(self.verifier.verify(generated).ok)

    def test_mixed_creates_copy_and_new_xml(self):
        plan = self._plan(AFTER)
        self.assertEqual(plan.xml_mode, "mixed")
        generated = self.generator.generate(plan)
        self.assertEqual(len(generated.files), 2)
        names = [file.relative_path.name for file in generated.files]
        self.assertIn("test.xml", names)
        self.assertIn("test_ace_proj.xml", names)
        self.assertTrue(self.verifier.verify(generated).ok)

    def test_resolved_edit_creates_dxml(self):
        plan = self._plan(AFTER, resolved=True)
        self.assertEqual(plan.xml_mode, "dxml_lua")
        self.assertTrue(plan.format == "dxml")
        generated = self.generator.generate(plan)
        self.assertEqual(len(generated.files), 1)
        self.assertTrue(
            generated.files[0].relative_path.name.startswith("modxml_")
        )
        self.assertIn("RegisterScriptCallback", generated.text)
        self.assertTrue(self.verifier.verify(generated).ok)

    def test_virtual_dxml_roundtrip(self):
        plan = self._plan(AFTER, resolved=True)
        generated = self.generator.generate(plan)
        verifier = XmlResolverPatchVerifier(XmlResolveService())
        result = verifier.verify(generated)
        self.assertTrue(result.ok, result.issues)

    def test_writer_accepts_xml_full_and_new_file(self):
        plan = self._plan(AFTER)
        generated = self.generator.generate(plan)
        generated = GeneratedPatch(
            plan=plan,
            files=generated.files,
            text=generated.text,
            verification=VerificationResult(ok=True),
        )
        writer = PatchWriter(require_verification=True)
        paths = writer.write(generated, Path(self.temp.name) / "mod")
        self.assertEqual(len(paths), 2)
        for path in paths:
            self.assertTrue(path.is_file())
            self.assertIn("configs", [part.lower() for part in path.parts])

    def test_writer_accepts_dxml_script_and_xml(self):
        plan = self._plan(AFTER, resolved=True)
        generated = self.generator.generate(plan)
        generated = GeneratedPatch(
            plan=generated.plan,
            files=generated.files,
            text=generated.text,
            verification=VerificationResult(ok=True),
        )
        writer = PatchWriter(require_verification=True)
        paths = writer.write(generated, Path(self.temp.name) / "mod")
        self.assertTrue(paths[0].is_file())
        self.assertIn("scripts", [part.lower() for part in paths[0].parts])

    def test_writer_blocks_unsafe_dxml_path(self):
        plan = self._plan(AFTER, resolved=True)
        generated = self.generator.generate(plan)
        bad_file = generated.files[0].__class__(
            Path("gamedata") / "scripts" / "not_modxml.script",
            "text",
            "utf-8-sig",
        )
        bad = GeneratedPatch(
            plan=generated.plan,
            files=(bad_file,),
            text="text",
            verification=VerificationResult(ok=True),
        )
        writer = PatchWriter(require_verification=True)
        with self.assertRaises(PatchWriteError):
            writer.write(bad, Path(self.temp.name) / "mod")


if __name__ == "__main__":
    unittest.main()
