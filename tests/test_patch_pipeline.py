# -*- coding: utf-8 -*-
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.assembly_service import AssemblyService
from core.documents.document_diff import diff_texts
from core.patching.dltx_generator import DltxGenerator
from core.facade import CoreServices
from core.file_cache import FileCacheSystem
from core.patching.patch_models import PatchOptions
from core.patching.patch_planner import PatchPlanner
from core.patching.patch_targets import patch_file_name, resolve_patch_target
from core.patching.patch_verifier import ModelPatchVerifier, ResolverPatchVerifier
from core.patching.patch_writer import PatchWriteError, PatchWriter
from core.reference_graph import DisabledCache, ReferenceGraphBuilder
from core.resolved_cache import ResolvedCache
from core.resolver_service import ResolverService
from core.tree_builder import TreeBuilder
from core.tree_query_service import TreeQueryService
from core.xml_resolve_service import XmlResolveService


class _Forest:
    def __init__(self, scan_dir, files, parents=None):
        self.scan_dir = str(scan_dir)
        self.files = [Path(p) for p in files]
        self.include_parents = {
            Path(k): {Path(v) for v in values}
            for k, values in (parents or {}).items()
        }


class PatchTargetTest(unittest.TestCase):
    def test_uses_root_not_immediate_parent(self):
        root = Path("C:/game/gamedata/configs/system.ltx")
        middle = Path("C:/game/gamedata/configs/items/a.ltx")
        leaf = Path("C:/game/gamedata/configs/items/b.ltx")

        forest = _Forest(
            root.parent,
            [root, middle, leaf],
            {
                middle: {root},
                leaf: {middle},
            },
        )

        target = resolve_patch_target(forest, leaf)

        self.assertTrue(target.resolved)
        self.assertEqual(target.root_path, root)

    def test_patch_name_preserves_dots_for_engine_attachment(self):
        self.assertEqual(
            patch_file_name(Path("C:/game/wpn.ak74.ltx"), "ace"),
            "mod_wpn.ak74_~ace.ltx",
        )

    def test_patch_suffix_sorts_after_existing_mods(self):
        from core.patching.patch_targets import choose_load_last_suffix

        root = Path("C:/game/system.ltx")
        self.assertEqual(
            choose_load_last_suffix(
                root,
                ["mod_system_old.ltx", "mod_system_aaa.ltx"],
                "ace",
            ),
            "~ace",
        )
        self.assertEqual(
            choose_load_last_suffix(
                root,
                ["mod_system_~zzz.ltx"],
                "ace",
            ),
            "~zzz~",
        )

    def test_ambiguous_when_multiple_roots(self):
        root1 = Path("C:/game/gamedata/configs/a.ltx")
        root2 = Path("C:/game/gamedata/configs/b.ltx")
        leaf = Path("C:/game/gamedata/configs/c.ltx")

        forest = _Forest(
            root1.parent,
            [root1, root2, leaf],
            {leaf: {root1, root2}},
        )

        target = resolve_patch_target(forest, leaf)

        self.assertFalse(target.resolved)
        self.assertTrue(target.ambiguous)
        self.assertEqual(set(target.candidates), {root1, root2})


class PatchPipelineTest(unittest.TestCase):
    def _forest(self, root, source):
        return _Forest(
            root.parent,
            [root, source],
            {source: {root}},
        )

    def test_plan_generate_and_verify_set_key(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "gamedata" / "configs"
            cfg.mkdir(parents=True)
            root = cfg / "system.ltx"
            source = cfg / "a.ltx"
            root.write_text('[system]\n#include "a.ltx"\n', encoding="cp1251")
            source.write_text('[weapon]\ncost = 100\n', encoding="cp1251")

            forest = self._forest(root, source)
            plan = PatchPlanner().plan_document_change(
                source,
                '[weapon]\ncost = 100\n',
                '[weapon]\ncost = 150\nnew_key = x\n',
                forest,
                PatchOptions(patch_mod="Test", suffix="test"),
            )

            self.assertFalse(plan.has_errors)
            self.assertEqual(plan.target.root_path, root)
            self.assertEqual(plan.patch_file_rel.name, "mod_system_~test.ltx")
            self.assertIn("![weapon]", plan.preview)
            self.assertIn("cost = 150", plan.preview)
            self.assertIn("new_key = x", plan.preview)

            generated = DltxGenerator().generate(plan)
            verification = ModelPatchVerifier().verify(generated)

            self.assertTrue(verification.ok)
            self.assertEqual(
                generated.files[0].relative_path,
                Path("gamedata") / "configs" / "mod_system_~test.ltx",
            )

    def test_new_section_uses_safe_create(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "gamedata" / "configs"
            cfg.mkdir(parents=True)
            root = cfg / "system.ltx"
            source = cfg / "a.ltx"
            root.write_text('[system]\n#include "a.ltx"\n', encoding="cp1251")
            source.write_text("[weapon]\ncost = 100\n", encoding="cp1251")

            plan = PatchPlanner().plan_document_change(
                source,
                "[weapon]\ncost = 100\n",
                "[weapon]\ncost = 100\n[new_section]:weapon\nfoo = bar\n",
                self._forest(root, source),
                PatchOptions(patch_mod="Test", suffix="new"),
            )

            self.assertIn("@[new_section]:weapon", plan.preview)
            generated = DltxGenerator().generate(plan)
            self.assertTrue(ModelPatchVerifier().verify(generated).ok)

    def test_delete_section_is_blocked_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "gamedata" / "configs"
            cfg.mkdir(parents=True)
            root = cfg / "system.ltx"
            source = cfg / "a.ltx"
            root.write_text('[system]\n#include "a.ltx"\n', encoding="cp1251")
            source.write_text("[a]\nx = 1\n[b]\ny = 2\n", encoding="cp1251")

            plan = PatchPlanner().plan_document_change(
                source,
                "[a]\nx = 1\n[b]\ny = 2\n",
                "[a]\nx = 1\n",
                self._forest(root, source),
                PatchOptions(patch_mod="Test", suffix="del"),
            )

            self.assertTrue(plan.has_errors)
            self.assertIn(
                "delete_section_disabled",
                {issue.code for issue in plan.errors()},
            )

            allowed = PatchPlanner().plan_document_change(
                source,
                "[a]\nx = 1\n[b]\ny = 2\n",
                "[a]\nx = 1\n",
                self._forest(root, source),
                PatchOptions(
                    patch_mod="Test",
                    suffix="del",
                    allow_delete_section=True,
                ),
            )
            self.assertFalse(allowed.has_errors)
            self.assertIn("!![b]", allowed.preview)

    def test_parent_removal_renders_and_verifies(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "gamedata" / "configs"
            cfg.mkdir(parents=True)
            root = cfg / "system.ltx"
            source = cfg / "a.ltx"
            root.write_text('[system]\n#include "a.ltx"\n', encoding="cp1251")
            source.write_text(
                "[base]\nx = 1\n[weapon]:base\ny = 2\n",
                encoding="cp1251",
            )

            plan = PatchPlanner().plan_document_change(
                source,
                "[base]\nx = 1\n[weapon]:base\ny = 2\n",
                "[base]\nx = 1\n[weapon]\ny = 2\n",
                self._forest(root, source),
                PatchOptions(patch_mod="Test", suffix="parent"),
            )

            self.assertIn("![weapon]:!base", plan.preview)
            generated = DltxGenerator().generate(plan)
            self.assertTrue(ModelPatchVerifier().verify(generated).ok)

    def test_include_change_blocks_generation(self):
        diff = diff_texts(
            '#include "a.ltx"\n[a]\nx = 1\n',
            '#include "b.ltx"\n[a]\nx = 1\n',
        )
        self.assertTrue(diff.has_blocking_issues)

    def test_writer_requires_verification_and_rejects_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "base"
            base.mkdir()
            root = base / "system.ltx"
            source = base / "a.ltx"
            root.write_text("[system]\n", encoding="cp1251")
            source.write_text("[a]\nx = 1\n", encoding="cp1251")

            plan = PatchPlanner().plan_document_change(
                source,
                "[a]\nx = 1\n",
                "[a]\nx = 2\n",
                _Forest(base, [root, source], {source: {root}}),
                PatchOptions(patch_mod="Test", suffix="write"),
            )
            generated = DltxGenerator().generate(plan)
            self.assertIsNone(generated.verification)

            writer = PatchWriter(require_verification=True)

            with self.assertRaises(PatchWriteError):
                writer.write(generated, Path(d) / "mods" / "Test Patch")

            verified = type(generated)(
                plan=generated.plan,
                files=generated.files,
                text=generated.text,
                verification=ModelPatchVerifier().verify(generated),
            )

            with self.assertRaises(PatchWriteError):
                writer.write(
                    verified,
                    Path(d) / "overwrite" / "Test Patch",
                )

            strict_writer = PatchWriter(
                require_verification=True,
                require_resolver=True,
            )
            with self.assertRaises(PatchWriteError):
                strict_writer.write(
                    verified,
                    Path(d) / "mods" / "Strict Patch",
                )

            written = writer.write(
                verified,
                Path(d) / "mods" / "Test Patch",
            )
            self.assertEqual(len(written), 1)
            self.assertTrue(written[0].is_file())
            self.assertIn(
                "x = 2",
                written[0].read_text(encoding="windows-1251"),
            )

    def test_generated_patch_is_applied_by_resolver4(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "gamedata" / "configs"
            cfg.mkdir(parents=True)
            root = cfg / "system.ltx"
            source = cfg / "a.ltx"
            root.write_text('[system]\n#include "a.ltx"\n', encoding="cp1251")
            source.write_text(
                '[weapon]\ncost = 100\nname = "Old"\n',
                encoding="cp1251",
            )

            cache = FileCacheSystem()
            forest = TreeBuilder(cache=cache).build(
                cfg,
                root,
                use_cache=False,
            )

            plan = PatchPlanner().plan_document_change(
                source,
                '[weapon]\ncost = 100\nname = "Old"\n',
                '[weapon]\ncost = 150\nname = "New"\n',
                forest,
                PatchOptions(patch_mod="Test", suffix="e2e"),
            )
            generated = DltxGenerator().generate(plan)

            patch_path = cfg / generated.files[0].relative_path.relative_to(
                Path("gamedata") / "configs"
            )
            patch_path.write_text(generated.text, encoding="cp1251")

            service = ResolverService(
                cache=cache,
                resolved_cache=ResolvedCache(),
            )
            text, _sources = service.assemble_resolved(root, forest, cfg)

            self.assertIn("cost = 150", text)
            self.assertIn('name = "New"', text)

    def test_writer_requires_confirmation_for_delete_key(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "base"
            base.mkdir()
            root = base / "system.ltx"
            source = base / "a.ltx"
            root.write_text("[system]\n", encoding="cp1251")
            source.write_text("[a]\nx = 1\ny = 2\n", encoding="cp1251")

            plan = PatchPlanner().plan_document_change(
                source,
                "[a]\nx = 1\ny = 2\n",
                "[a]\nx = 1\n",
                _Forest(base, [root, source], {source: {root}}),
                PatchOptions(patch_mod="Test", suffix="confirm"),
            )
            generated = DltxGenerator().generate(plan)
            verified = replace(
                generated,
                verification=ModelPatchVerifier().verify(generated),
            )
            writer = PatchWriter(require_verification=True)

            with self.assertRaises(PatchWriteError):
                writer.write(verified, Path(d) / "mods" / "Needs Confirm")

            confirmed_plan = replace(plan, confirmed=True)
            confirmed_generated = DltxGenerator().generate(confirmed_plan)
            confirmed = replace(
                confirmed_generated,
                verification=ModelPatchVerifier().verify(
                    confirmed_generated
                ),
            )
            written = writer.write(
                confirmed,
                Path(d) / "mods" / "Confirmed",
            )
            self.assertEqual(len(written), 1)

    def test_single_line_plan_supports_full_dltx_set(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "a.ltx"
            source.write_text("[a]\nx = 1\n", encoding="cp1251")
            forest = _Forest(d, [source])

            cases = [
                (
                    "value",
                    "[a]\nx = 1\n",
                    "[a]\nx = 2\n",
                    "x = 2",
                    {},
                ),
                (
                    "rename",
                    "[a]\nx = 1\n",
                    "[a]\ny = 1\n",
                    "!x",
                    {},
                ),
                (
                    "delete",
                    "[a]\nx = 1\n",
                    "[a]\n\n",
                    "!x",
                    {},
                ),
                (
                    "add",
                    "[a]\n\n",
                    "[a]\nx = 1\n",
                    "x = 1",
                    {},
                ),
                (
                    "list_append",
                    "[a]\nlist = a,b\n\n",
                    "[a]\nlist = a,b\n>list = c\n",
                    ">list = c",
                    {},
                ),
                (
                    "parent",
                    "[a]\nx = 1\n",
                    "[a]:base\nx = 1\n",
                    "![a]:base",
                    {},
                ),
                (
                    "delete_section",
                    "[a]\nx = 1\n",
                    "!![a]\nx = 1\n",
                    "!![a]",
                    {"allow_delete_section": True},
                ),
                (
                    "add_section",
                    "[a]\nx = 1\n\n",
                    "[a]\nx = 1\n[new]\n",
                    "@[new]",
                    {},
                ),
            ]

            for name, baseline, edited, expected, extra in cases:
                with self.subTest(name=name):
                    plan = PatchPlanner().plan_single_line_edit(
                        source,
                        baseline,
                        edited,
                        forest=forest,
                        options=PatchOptions(
                            patch_mod="Test",
                            suffix="line",
                            **extra,
                        ),
                    )
                    self.assertFalse(
                        plan.has_errors,
                        [issue.message for issue in plan.errors()],
                    )
                    self.assertIn(expected, plan.preview)

                    generated = DltxGenerator().generate(plan)
                    verification = ModelPatchVerifier().verify(generated)
                    self.assertTrue(
                        verification.ok,
                        [check.name for check in verification.failed_checks],
                    )

    def test_core_services_pipeline_generates_verified_patch(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "gamedata" / "configs"
            cfg.mkdir(parents=True)
            root = cfg / "system.ltx"
            source = cfg / "a.ltx"
            root.write_text('[system]\n#include "a.ltx"\n', encoding="cp1251")
            source.write_text("[w]\ncost = 100\n", encoding="cp1251")

            cache = FileCacheSystem()
            forest = TreeBuilder(cache).build(
                cfg,
                root,
                use_cache=False,
            )
            resolver = ResolverService(cache, ResolvedCache())
            resolver.ensure_adapter(forest, cfg)

            services = CoreServices(
                cache=cache,
                tree_builder=TreeBuilder(cache),
                tree_query=TreeQueryService(),
                graph_builder=ReferenceGraphBuilder(DisabledCache()),
                resolver_service=resolver,
                assembly_service=AssemblyService(
                    resolver,
                    Path(d) / "generated",
                ),
                generated_dir=Path(d) / "generated",
                xml_resolve_service=XmlResolveService(),
            )

            plan = services.plan_document_patch(
                source,
                "[w]\ncost = 100\n",
                "[w]\ncost = 150\n",
                forest,
                PatchOptions(patch_mod="Test", suffix="facade"),
            )
            generated = services.generate_patch(plan)

            self.assertTrue(generated.verification.ok)
            written = services.write_patch(
                generated,
                Path(d) / "mods" / "Test Patch",
                forbidden_roots=[cfg],
            )
            self.assertEqual(len(written), 1)
            self.assertIn(
                "cost = 150",
                written[0].read_text(encoding="windows-1251"),
            )

    def test_dotted_root_patch_is_seen_by_resolver(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "gamedata" / "configs"
            cfg.mkdir(parents=True)
            root = cfg / "wpn.ak74.ltx"
            (cfg / "system.ltx").write_text("[system]\n", encoding="cp1251")
            root.write_text(
                "[wpn_ak74]\ncost = 100\n",
                encoding="cp1251",
            )

            cache = FileCacheSystem()
            forest = TreeBuilder(cache=cache).build(
                cfg,
                cfg / "system.ltx",
                use_cache=False,
            )
            service = ResolverService(
                cache=cache,
                resolved_cache=ResolvedCache(),
            )
            adapter = service.ensure_adapter(forest, cfg)

            plan = PatchPlanner().plan_document_change(
                root,
                "[wpn_ak74]\ncost = 100\n",
                "[wpn_ak74]\ncost = 150\n",
                forest,
                PatchOptions(patch_mod="Test", suffix="ace"),
            )
            generated = DltxGenerator().generate(plan)

            self.assertEqual(
                plan.patch_file_rel.name,
                "mod_wpn.ak74_~ace.ltx",
            )
            self.assertTrue(
                ResolverPatchVerifier(adapter).verify(generated).ok
            )

    def test_resolver_verifier_sees_virtual_patch(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "gamedata" / "configs"
            cfg.mkdir(parents=True)
            root = cfg / "system.ltx"
            source = cfg / "a.ltx"
            root.write_text('[system]\n#include "a.ltx"\n', encoding="cp1251")
            source.write_text(
                '[weapon]\ncost = 100\nname = "Old"\n',
                encoding="cp1251",
            )

            cache = FileCacheSystem()
            forest = TreeBuilder(cache=cache).build(
                cfg,
                root,
                use_cache=False,
            )
            service = ResolverService(
                cache=cache,
                resolved_cache=ResolvedCache(),
            )
            adapter = service.ensure_adapter(forest, cfg)

            plan = PatchPlanner().plan_document_change(
                source,
                '[weapon]\ncost = 100\nname = "Old"\n',
                '[weapon]\ncost = 150\nname = "New"\n',
                forest,
                PatchOptions(patch_mod="Test", suffix="resolver"),
            )
            generated = DltxGenerator().generate(plan)

            verification = ResolverPatchVerifier(adapter).verify(generated)

            self.assertTrue(verification.ok, verification.issues)
            self.assertTrue(
                any(
                    check.name == "resolver:weapon:cost" and check.ok
                    for check in verification.checks
                )
            )

            from core.patching.patch_models import PatchFile

            bad_text = generated.text.replace("cost = 150", "cost = 999")
            bad_file = replace(
                generated.files[0],
                text=bad_text,
            )
            bad = replace(
                generated,
                text=bad_text,
                files=(bad_file,),
            )
            bad_verification = ResolverPatchVerifier(adapter).verify(bad)

            self.assertFalse(bad_verification.ok)
            self.assertTrue(bad_verification.failed_checks)


if __name__ == "__main__":
    unittest.main()
