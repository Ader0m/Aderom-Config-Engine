# -*- coding: utf-8 -*-
"""Эталонный набор release gate: моды, список, удаление, создание,
XML/DXML и полная запись через ``PatchCommitService``."""

import hashlib
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.facade import CoreServices
from core.patching.commit import PatchCommitError, PatchCommitService
from core.patching.ltx.document import parse_ltx_document
from core.patching.models import PatchOptions
from core.patching.planner import PatchPlanner
from core.patching.writer import PatchWriteError, PatchWriter
from core.patching.xml.generator import DxmlGenerator
from core.patching.xml.verifier import (
    XmlModelPatchVerifier,
    XmlResolverPatchVerifier,
)
from core.resolver.assembly import AssemblyService
from core.resolver.service import ResolverService
from core.tools.cache import FileCacheSystem, ResolvedCache
from core.tree.builder import TreeBuilder
from core.tree.graph import DisabledCache, ReferenceGraphBuilder
from core.tree.query import TreeQueryService
from core.xml.service import XmlResolveService


class _XmlForest:
    def __init__(self, config_dir, xml_files):
        self.scan_dir = str(config_dir)
        self.files = []
        self.xml_files = list(xml_files)
        self.include_parents = {}
        self.roots = []


def _file_hashes(root):
    root = Path(root)
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def _services(config_dir, generated_dir):
    cache = FileCacheSystem()
    forest = TreeBuilder(cache=cache).build(
        config_dir,
        config_dir / "system.ltx",
        use_cache=False,
    )
    resolver = ResolverService(cache, ResolvedCache())
    resolver.ensure_adapter(forest, config_dir)
    services = CoreServices(
        cache=cache,
        tree_builder=TreeBuilder(cache),
        tree_query=TreeQueryService(),
        graph_builder=ReferenceGraphBuilder(DisabledCache()),
        resolver_service=resolver,
        assembly_service=AssemblyService(resolver, generated_dir),
        generated_dir=generated_dir,
        xml_resolve_service=XmlResolveService(),
    )
    return forest, services


class ReleaseGateReferenceTest(unittest.TestCase):
    def test_ltx_reference_set_full_commit_and_reapply(self):
        with tempfile.TemporaryDirectory() as d:
            configs = Path(d) / "gamedata" / "configs"
            configs.mkdir(parents=True)
            (configs / "system.ltx").write_text(
                "[system]\n"
                '#include "base.ltx"\n'
                '#include "mod_a.ltx"\n'
                '#include "mod_b.ltx"\n',
                encoding="cp1251",
            )
            (configs / "base.ltx").write_text(
                "[base]\n"
                "value = 1\n"
                "[weapon]:base\n"
                "cost = 100\n"
                "[list]\n"
                "items = a\n"
                "[ammo]\n"
                "count = 1\n"
                "[deleted_by_mod]\n"
                "gone = 1\n"
                "[removed]\n"
                "gone = 1\n",
                encoding="cp1251",
            )
            (configs / "mod_a.ltx").write_text(
                "@[weapon]\n"
                "cost = 110\n"
                "@[list]\n"
                ">items = b\n",
                encoding="cp1251",
            )
            (configs / "mod_b.ltx").write_text(
                "![ammo]\n"
                "count = 10\n"
                "!![deleted_by_mod]\n"
                "[created]\n"
                "fresh = 1\n",
                encoding="cp1251",
            )
            original_hashes = _file_hashes(configs)
            source = configs / "mod_a.ltx"
            before = source.read_text(encoding="cp1251")
            after = (
                "@[weapon]\n"
                "cost = 120\n"
                "@[list]\n"
                ">items = b\n"
                ">items = c\n"
                "@[created_extra]:weapon\n"
                "fresh = 1\n"
            )
            forest, services = _services(
                configs,
                Path(d) / "generated",
            )
            plan = services.plan_document_patch(
                source,
                before,
                after,
                forest,
                PatchOptions(
                    patch_mod="Test",
                    suffix="gate",
                    snapshot_id="SNAP-LTX",
                    allow_delete_section=True,
                ),
            )
            self.assertFalse(plan.has_errors, plan.issues)
            generated = services.generate_patch(plan)
            self.assertTrue(
                generated.verification.ok,
                generated.verification.issues,
            )
            report = services.patch_commit.roundtrip.verify(generated)
            self.assertTrue(report.ok, report.issues)
            authorization = services.patch_commit.authorize(
                generated,
                confirmed_risks=services.patch_commit.risk_keys(plan),
            )
            with self.assertRaises(PatchWriteError):
                services.patch_commit.commit(
                    generated,
                    authorization,
                    configs,
                    expected_snapshot_id="SNAP-LTX",
                    forbidden_roots=[configs],
                )
            self.assertEqual(_file_hashes(configs), original_hashes)
            patch_mod = Path(d) / "mods" / "Test Patch"
            result = services.patch_commit.commit(
                generated,
                authorization,
                patch_mod,
                expected_snapshot_id="SNAP-LTX",
                forbidden_roots=[configs],
            )

            self.assertEqual(_file_hashes(configs), original_hashes)
            self.assertTrue(result.paths)
            for path in result.paths:
                self.assertTrue(
                    Path(path).resolve().is_relative_to(patch_mod.resolve())
                )

            base_source = configs / "base.ltx"
            before_base = base_source.read_text(encoding="cp1251")
            after_base = before_base.replace(
                "[removed]\ngone = 1\n",
                "",
            )
            delete_plan = services.plan_document_patch(
                base_source,
                before_base,
                after_base,
                forest,
                PatchOptions(
                    patch_mod="Test",
                    suffix="delete",
                    snapshot_id="SNAP-LTX",
                    allow_delete_section=True,
                ),
            )
            self.assertFalse(delete_plan.has_errors, delete_plan.issues)
            delete_generated = services.generate_patch(delete_plan)
            self.assertTrue(
                delete_generated.verification.ok,
                delete_generated.verification.issues,
            )
            delete_authorization = services.patch_commit.authorize(
                delete_generated,
                confirmed_risks=services.patch_commit.risk_keys(
                    delete_plan
                ),
            )
            delete_result = services.patch_commit.commit(
                delete_generated,
                delete_authorization,
                patch_mod,
                expected_snapshot_id="SNAP-LTX",
                forbidden_roots=[configs],
            )
            self.assertEqual(_file_hashes(configs), original_hashes)
            written_paths = result.paths + delete_result.paths
            self.assertEqual(len(written_paths), 2)
            for path in written_paths:
                self.assertTrue(
                    Path(path).resolve().is_relative_to(patch_mod.resolve())
                )

            with self.assertRaises(PatchCommitError):
                services.patch_commit.commit(
                    generated,
                    authorization,
                    Path(d) / "mods" / "Stale",
                    expected_snapshot_id="OTHER",
                    forbidden_roots=[configs],
                )
            self.assertFalse((Path(d) / "mods" / "Stale").exists())

            applied_root = Path(d) / "applied"
            applied_configs = applied_root / "gamedata" / "configs"
            shutil.copytree(configs, applied_configs)
            for path in written_paths:
                relative = Path(path).relative_to(patch_mod)
                target = applied_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                if relative.parent == Path("gamedata") / "configs":
                    include = relative.name
                    root_path = applied_configs / "system.ltx"
                    root_path.write_text(
                        root_path.read_text(encoding="cp1251").rstrip()
                        + '\n#include "%s"\n' % include,
                        encoding="cp1251",
                    )

            applied_cache = FileCacheSystem()
            applied_forest = TreeBuilder(cache=applied_cache).build(
                applied_configs,
                applied_configs / "system.ltx",
                use_cache=False,
            )
            applied_resolver = ResolverService(
                applied_cache,
                ResolvedCache(),
            )
            text, _sources = applied_resolver.assemble_resolved(
                applied_configs / "system.ltx",
                applied_forest,
                applied_configs,
            )
            document = parse_ltx_document(text)
            self.assertEqual(document.values("weapon").get("cost"), "120")
            items = document.values("list").get("items") or ""
            self.assertIn("b", items)
            self.assertIn("c", items)
            self.assertEqual(
                document.values("ammo").get("count"),
                "10",
            )
            self.assertEqual(
                document.values("created").get("fresh"),
                "1",
            )
            self.assertIsNone(document.section("deleted_by_mod"))
            self.assertEqual(
                document.values("created_extra").get("fresh"),
                "1",
            )
            self.assertIsNone(document.section("removed"))

    def test_xml_dxml_include_full_commit(self):
        # DXML — Lua-скрипт для игрового XmlResolver. Виртуальный
        # verifier применяет include + патч; пост-запись XML проверяется
        # отдельным full-copy тестом ниже.
        with tempfile.TemporaryDirectory() as d:
            configs = Path(d) / "gamedata" / "configs"
            scripts = Path(d) / "gamedata" / "scripts"
            (configs / "ui" / "parts").mkdir(parents=True)
            scripts.mkdir(parents=True)
            (scripts / "dxml_core.script").write_text(
                "-- core\n",
                encoding="utf-8",
            )
            child = configs / "ui" / "parts" / "a.xml"
            child.write_text('<a x="1"/>\n', encoding="cp1251")
            main = configs / "ui" / "main.xml"
            main.write_text(
                '<root>\n#include "ui\\parts\\*.xml"\n</root>\n',
                encoding="cp1251",
            )
            original = (main.read_bytes(), child.read_bytes())
            xml_service = XmlResolveService(scripts_dir=scripts)
            before = xml_service.resolve_output(main, configs).text
            self.assertIn('<a x="1"/>', before)
            after = before.replace('x="1"', 'x="2"')
            self.assertIn('x="2"', after)

            forest = _XmlForest(configs, [main, child])
            plan = PatchPlanner().plan_xml_resolved_edit(
                child,
                before,
                after,
                forest=forest,
                options=PatchOptions(
                    project_tag="gate",
                    snapshot_id="SNAP-XML-INCLUDE",
                ),
            )
            self.assertFalse(plan.has_errors, plan.issues)
            self.assertEqual(plan.xml_mode, "dxml_lua")
            generated = DxmlGenerator().generate(plan)
            verified = replace(
                generated,
                verification=XmlModelPatchVerifier().verify(generated),
            )
            self.assertTrue(
                verified.verification.ok,
                verified.verification.issues,
            )
            roundtrip = XmlResolverPatchVerifier(xml_service)
            report = roundtrip.verify(verified)
            self.assertTrue(report.ok, report.issues)
            self.assertIn(
                "resolver",
                [check.name for check in report.checks],
            )
            service = PatchCommitService(
                PatchWriter(
                    require_verification=True,
                    require_resolver=False,
                ),
                roundtrip,
            )
            authorization = service.authorize(verified)
            patch_mod = Path(d) / "mods" / "XML DXML Gate"
            result = service.commit(
                verified,
                authorization,
                patch_mod,
                expected_snapshot_id="SNAP-XML-INCLUDE",
                forbidden_roots=[configs],
            )

            self.assertEqual(
                (main.read_bytes(), child.read_bytes()),
                original,
            )
            self.assertTrue(result.paths)
            for path in result.paths:
                self.assertTrue(Path(path).is_file())
                self.assertTrue(
                    Path(path).resolve().is_relative_to(patch_mod.resolve())
                )
            script = Path(result.paths[0]).read_text(encoding="utf-8-sig")
            self.assertIn("RegisterScriptCallback", script)

    def test_xml_full_copy_reapply_in_new_vfs(self):
        with tempfile.TemporaryDirectory() as d:
            configs = Path(d) / "gamedata" / "configs"
            (configs / "ui").mkdir(parents=True)
            source = configs / "ui" / "test.xml"
            before = (
                "<root>\n"
                '    <item id="a" width="10">old</item>\n'
                '    <item id="b" width="20">keep</item>\n'
                "</root>\n"
            )
            after = (
                "<root>\n"
                '    <item id="a" width="12">new</item>\n'
                '    <item id="b" width="20">keep</item>\n'
                '    <item id="c" width="30">added</item>\n'
                "</root>\n"
            )
            source.write_bytes(before.encode("windows-1251"))
            original = source.read_bytes()
            forest = _XmlForest(configs, [source])
            plan = PatchPlanner().plan_xml_document_change(
                source,
                before,
                after,
                forest=forest,
                options=PatchOptions(
                    project_tag="gate",
                    snapshot_id="SNAP-XML-FULL",
                ),
            )
            self.assertFalse(plan.has_errors, plan.issues)
            self.assertEqual(plan.xml_mode, "mixed")
            generated = DxmlGenerator().generate(plan)
            verifier = XmlModelPatchVerifier()
            verified = replace(
                generated,
                verification=verifier.verify(generated),
            )
            self.assertTrue(
                verified.verification.ok,
                verified.verification.issues,
            )
            service = PatchCommitService(
                PatchWriter(
                    require_verification=True,
                    require_resolver=False,
                ),
                verifier,
            )
            authorization = service.authorize(verified)
            patch_mod = Path(d) / "mods" / "XML Full Gate"
            result = service.commit(
                verified,
                authorization,
                patch_mod,
                expected_snapshot_id="SNAP-XML-FULL",
                forbidden_roots=[configs],
            )
            self.assertEqual(source.read_bytes(), original)

            applied_root = Path(d) / "applied"
            applied_configs = applied_root / "gamedata" / "configs"
            shutil.copytree(configs, applied_configs)
            for path in result.paths:
                relative = Path(path).relative_to(patch_mod)
                target = applied_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)

            full_file = next(
                file for file in generated.files
                if file.relative_path.name == "test.xml"
            )
            new_file = next(
                file for file in generated.files
                if file.relative_path.name != "test.xml"
            )
            full_text = (
                applied_root / full_file.relative_path
            ).read_bytes().decode(full_file.encoding)
            new_text = (
                applied_root / new_file.relative_path
            ).read_bytes().decode(new_file.encoding)
            self.assertIn('width="12"', full_text)
            self.assertIn(">new<", full_text)
            self.assertNotIn('id="c"', full_text)
            self.assertIn('id="c"', new_text)
            self.assertIn(">added<", new_text)


if __name__ == "__main__":
    unittest.main()
