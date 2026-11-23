# -*- coding: utf-8 -*-
"""Тесты контракта и форматных движков Этапа 5.1."""

import tempfile
import unittest
from dataclasses import FrozenInstanceError, dataclass
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.api.patches import (
    GeneratedPatch,
    PatchBundle,
    PatchIntent,
    PatchIssue,
    PatchOptions,
    PatchPlan,
    PatchPlanningError,
    PatchResult,
    PatchSnapshot,
    PatchTarget,
    PatchFile,
    VerificationResult,
)
from core.facade import CoreServices
from core.patching.engines.asset import AssetPatchEngine
from core.patching.engines.ltx import LtxPatchEngine
from core.patching.engines.new_file import NewFilePatchEngine
from core.patching.engines.xml import XmlPatchEngine
from core.patching.engine import PatchOrchestrator
from core.resolver.assembly import AssemblyService
from core.resolver.service import ResolverService
from core.tools.cache import FileCacheSystem, ResolvedCache
from core.tree.builder import TreeBuilder
from core.tree.graph import DisabledCache, ReferenceGraphBuilder
from core.tree.query import TreeQueryService
from core.xml.service import XmlResolveService


@dataclass(frozen=True)
class _Change:
    kind: str
    path: Path
    description: str
    risk: str = "safe"


def _services(d, cfg, root):
    cache = FileCacheSystem()
    forest = TreeBuilder(cache).build(
        cfg,
        root,
        use_cache=False,
    )
    resolver = ResolverService(
        cache,
        ResolvedCache(cache_dir=Path(d) / "cache"),
    )
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
    return services, forest


def _ltx_environment(d):
    cfg = Path(d) / "gamedata" / "configs"
    cfg.mkdir(parents=True)
    root = cfg / "system.ltx"
    source = cfg / "a.ltx"
    root.write_text(
        '[system]\n#include "a.ltx"\n',
        encoding="cp1251",
    )
    source.write_text("[w]\ncost = 100\n", encoding="cp1251")
    services, forest = _services(d, cfg, root)
    return services, forest, source


class PatchIntentTest(unittest.TestCase):
    def test_rejects_unknown_kind(self):
        with self.assertRaises(ValueError):
            PatchIntent(kind="unknown", source_path=Path("a.ltx"))

    def test_rejects_missing_source(self):
        with self.assertRaises(ValueError):
            PatchIntent(kind="ltx", source_path=None)

    def test_intent_is_frozen(self):
        intent = PatchIntent(kind="ltx", source_path=Path("a.ltx"))
        with self.assertRaises(FrozenInstanceError):
            intent.kind = "xml"


class PatchOrchestratorTest(unittest.TestCase):
    def test_dispatch_by_kind(self):
        with tempfile.TemporaryDirectory() as d:
            services, _forest, _source = _ltx_environment(d)

            orchestrator = services.patch_orchestrator
            self.assertIsInstance(
                orchestrator.engine("ltx"),
                LtxPatchEngine,
            )
            self.assertIsInstance(
                orchestrator.engine("xml"),
                XmlPatchEngine,
            )
            self.assertIsInstance(
                orchestrator.engine("asset"),
                AssetPatchEngine,
            )
            self.assertIsInstance(
                orchestrator.engine("new_file"),
                NewFilePatchEngine,
            )

            with self.assertRaises(ValueError):
                orchestrator.engine("unknown")

    def test_build_ltx_bundle(self):
        with tempfile.TemporaryDirectory() as d:
            services, forest, source = _ltx_environment(d)

            intent = PatchIntent(
                kind="ltx",
                source_path=source,
                before_text="[w]\ncost = 100\n",
                after_text="[w]\ncost = 150\n",
                patch_mod="Test",
                options=PatchOptions(
                    patch_mod="FromOptions",
                    suffix="engine",
                ),
            )
            snapshot = PatchSnapshot(
                forest=forest,
                snapshot_id="SNAP-X",
            )
            bundle = services.patch_orchestrator.build(intent, snapshot)

            self.assertTrue(bundle.ok)
            self.assertEqual(bundle.intent, intent)
            self.assertEqual(bundle.snapshot, snapshot)
            self.assertTrue(bundle.plan.can_generate)
            self.assertEqual(bundle.plan.patch_mod, "Test")
            self.assertEqual(bundle.plan.snapshot_id, "SNAP-X")
            self.assertIsNotNone(bundle.generated)
            self.assertIs(bundle.generated.plan, bundle.plan)
            self.assertEqual(bundle.generated.plan.snapshot_id, "SNAP-X")
            self.assertTrue(bundle.verification.ok)
            self.assertTrue(bundle.generated.files)

            engine = services.patch_orchestrator.engine("ltx")
            self.assertIn("cost = 150", engine.preview(bundle.plan))
            regenerated = engine.generate(bundle.plan)
            self.assertTrue(engine.verify(regenerated).ok)

    def test_intent_patch_mod_without_options(self):
        with tempfile.TemporaryDirectory() as d:
            services, forest, source = _ltx_environment(d)
            snapshot = PatchSnapshot(forest=forest, snapshot_id="SNAP-X")

            intent = PatchIntent(
                kind="ltx",
                source_path=source,
                before_text="[w]\ncost = 100\n",
                after_text="[w]\ncost = 150\n",
                patch_mod="OnlyIntent",
            )
            bundle = services.patch_orchestrator.build(intent, snapshot)
            self.assertEqual(bundle.plan.patch_mod, "OnlyIntent")

            fallback = PatchIntent(
                kind="ltx",
                source_path=source,
                before_text="[w]\ncost = 100\n",
                after_text="[w]\ncost = 150\n",
                options=PatchOptions(
                    patch_mod="FromOptions",
                    suffix="engine",
                ),
            )
            bundle = services.patch_orchestrator.build(fallback, snapshot)
            self.assertEqual(bundle.plan.patch_mod, "FromOptions")

    def test_empty_snapshot_keeps_options_snapshot_id(self):
        with tempfile.TemporaryDirectory() as d:
            services, forest, source = _ltx_environment(d)
            intent = PatchIntent(
                kind="ltx",
                source_path=source,
                before_text="[w]\ncost = 100\n",
                after_text="[w]\ncost = 150\n",
                options=PatchOptions(snapshot_id="LEGACY"),
            )
            bundle = services.patch_orchestrator.build(
                intent,
                PatchSnapshot(forest=forest),
            )
            self.assertEqual(bundle.plan.snapshot_id, "LEGACY")

    def test_snapshot_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            services, forest, source = _ltx_environment(d)
            intent = PatchIntent(
                kind="ltx",
                source_path=source,
                before_text="[w]\ncost = 100\n",
                after_text="[w]\ncost = 150\n",
                options=PatchOptions(snapshot_id="OTHER"),
            )
            snapshot = PatchSnapshot(
                forest=forest,
                snapshot_id="SNAP-X",
            )
            with self.assertRaises(PatchPlanningError):
                services.patch_orchestrator.build(intent, snapshot)

class _FakeXmlServices:
    def __init__(self):
        self.calls = []
        self.patch = self

    def plan_xml_patch(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        source = Path(args[0])
        options = kwargs["options"]
        plan = PatchPlan(
            source_path=source,
            target=PatchTarget(source_path=source, root_path=None),
            patch_mod=options.patch_mod,
            patch_file_rel=Path("x.xml"),
            snapshot_id=options.snapshot_id,
        )
        generated = GeneratedPatch(
            plan=plan,
            files=(
                PatchFile(
                    relative_path=Path("gamedata/configs/x.xml"),
                    text="<x/>",
                ),
            ),
            text="<x/>",
            verification=VerificationResult(ok=True),
        )
        return PatchResult(plan=plan, generated=generated)

    def generate_patch(self, plan):
        raise AssertionError("generate_patch не должен вызываться")

    def verify_patch(self, generated):
        return generated.verification

    def verify_patch_with_resolver(self, generated):
        return generated.verification

    def preview(self, plan):
        return plan.preview


class XmlPatchEngineTest(unittest.TestCase):
    def test_plan_maps_xml_fields(self):
        services = _FakeXmlServices()
        engine = XmlPatchEngine(services)
        overlay = object()
        intent = PatchIntent(
            kind="xml",
            source_path=Path("C:/game/gamedata/configs/x.xml"),
            before_text="<x/>",
            after_text="<x><y/></x>",
            patch_mod="FromIntent",
            mode="dxml_lua",
            line_sources=(("1", "x"),),
            project_mode="override",
            overlay=overlay,
        )
        snapshot = PatchSnapshot(snapshot_id="SNAP-X")
        result = engine.plan(intent, snapshot)

        self.assertTrue(result.generated.verification.ok)
        self.assertEqual(result.plan.patch_mod, "FromIntent")
        self.assertEqual(result.plan.snapshot_id, "SNAP-X")
        self.assertEqual(
            result.generated.plan.snapshot_id,
            "SNAP-X",
        )
        _args, kwargs = services.calls[0]
        self.assertEqual(kwargs["mode"], "dxml_lua")
        self.assertEqual(kwargs["project_mode"], "override")
        self.assertIs(kwargs["overlay"], overlay)
        self.assertEqual(kwargs["line_sources"], (("1", "x"),))
        self.assertEqual(kwargs["options"].patch_mod, "FromIntent")
        self.assertEqual(kwargs["options"].snapshot_id, "SNAP-X")
        self.assertEqual(engine.preview(result.plan), "")

    def test_snapshot_mismatch_is_rejected(self):
        services = _FakeXmlServices()
        engine = XmlPatchEngine(services)
        intent = PatchIntent(
            kind="xml",
            source_path=Path("C:/game/gamedata/configs/x.xml"),
            options=PatchOptions(snapshot_id="OTHER"),
        )
        with self.assertRaises(PatchPlanningError):
            engine.plan(
                intent,
                PatchSnapshot(snapshot_id="SNAP-X"),
            )
        self.assertEqual(services.calls, [])


class AssetPatchEngineTest(unittest.TestCase):
    def test_plan_builds_whole_file_patch(self):
        change = _Change(
            kind="asset",
            path=Path("x.ogg"),
            description="Заменить файл целиком",
            risk="confirm",
        )
        intent = PatchIntent(
            kind="asset",
            source_path=Path("x.ogg"),
            patch_mod="Test",
            relative_path=Path("sounds/x.ogg"),
            patch_root=Path("C:/game"),
            vfs_root="sounds",
            asset_save_mode="override",
            asset_data=b"OggS",
            changes=(change,),
        )
        engine = AssetPatchEngine()
        snapshot = PatchSnapshot(snapshot_id="SNAP-A")
        result = engine.plan(intent, snapshot)

        self.assertEqual(result.plan.format, "asset")
        self.assertEqual(result.plan.asset_mode, "override")
        self.assertEqual(result.plan.patch_target_dir, "sounds")
        self.assertEqual(result.plan.patch_mod, "Test")
        self.assertEqual(result.plan.snapshot_id, "SNAP-A")
        self.assertEqual(
            result.generated.plan.snapshot_id,
            "SNAP-A",
        )
        self.assertTrue(result.plan.needs_confirmation)
        self.assertEqual(
            result.generated.files[0].relative_path,
            Path("gamedata/sounds/x.ogg"),
        )
        self.assertEqual(result.generated.files[0].data, b"OggS")
        self.assertTrue(result.generated.verification.ok)
        self.assertEqual(engine.preview(result.plan), "")

        with self.assertRaises(PatchPlanningError):
            engine.generate(result.plan)

    def test_snapshot_mismatch_is_rejected(self):
        intent = PatchIntent(
            kind="asset",
            source_path=Path("x.ogg"),
            options=PatchOptions(snapshot_id="OTHER"),
            relative_path=Path("sounds/x.ogg"),
            asset_data=b"x",
        )
        with self.assertRaises(PatchPlanningError):
            AssetPatchEngine().plan(
                intent,
                PatchSnapshot(snapshot_id="SNAP-A"),
            )

    def test_rejects_path_outside_patch_mod(self):
        engine = AssetPatchEngine()
        for relative in (Path("../x.ogg"), Path("C:/x.ogg")):
            with self.subTest(relative=relative):
                intent = PatchIntent(
                    kind="asset",
                    source_path=Path("x.ogg"),
                    relative_path=relative,
                    asset_data=b"x",
                )
                with self.assertRaises(PatchPlanningError):
                    engine.plan(intent, PatchSnapshot())

    def test_options_patch_mod_is_used_when_intent_has_none(self):
        intent = PatchIntent(
            kind="asset",
            source_path=Path("x.ogg"),
            options=PatchOptions(patch_mod="FromOptions"),
            relative_path=Path("sounds/x.ogg"),
            patch_root=Path("C:/game"),
            vfs_root="sounds",
            asset_data=b"OggS",
        )
        result = AssetPatchEngine().plan(
            intent,
            PatchSnapshot(),
        )
        self.assertEqual(result.plan.patch_mod, "FromOptions")

    def test_options_snapshot_id_is_used_when_snapshot_is_empty(self):
        intent = PatchIntent(
            kind="asset",
            source_path=Path("x.ogg"),
            options=PatchOptions(snapshot_id="LEGACY"),
            relative_path=Path("sounds/x.ogg"),
            patch_root=Path("C:/game"),
            vfs_root="sounds",
            asset_data=b"OggS",
        )
        result = AssetPatchEngine().plan(
            intent,
            PatchSnapshot(),
        )
        self.assertEqual(result.plan.snapshot_id, "LEGACY")
        self.assertEqual(
            result.generated.plan.snapshot_id,
            "LEGACY",
        )

    def test_orchestrator_builds_asset_bundle(self):
        intent = PatchIntent(
            kind="asset",
            source_path=Path("x.ogg"),
            patch_mod="Test",
            relative_path=Path("sounds/x.ogg"),
            patch_root=Path("C:/game"),
            vfs_root="sounds",
            asset_save_mode="override",
            asset_data=b"OggS",
        )
        orchestrator = PatchOrchestrator(
            engines=(AssetPatchEngine(),)
        )
        bundle = orchestrator.build(
            intent,
            PatchSnapshot(snapshot_id="SNAP-A"),
        )
        self.assertTrue(bundle.ok)
        self.assertEqual(bundle.plan.snapshot_id, "SNAP-A")
        self.assertEqual(
            bundle.generated.plan.snapshot_id,
            "SNAP-A",
        )


class NewFilePatchEngineTest(unittest.TestCase):
    def test_plan_and_generate_new_ltx_file(self):
        text = "[new]\nx = 1\n"
        intent = PatchIntent(
            kind="new_file",
            source_path=Path("new.ltx"),
            patch_mod="Test",
            after_text=text,
            relative_path=Path("new.ltx"),
        )
        engine = NewFilePatchEngine()
        snapshot = PatchSnapshot(snapshot_id="SNAP-N")
        result = engine.plan(intent, snapshot)

        self.assertEqual(result.plan.format, "ltx")
        self.assertEqual(result.plan.patch_mod, "Test")
        self.assertEqual(result.plan.snapshot_id, "SNAP-N")
        self.assertEqual(
            result.generated.plan.snapshot_id,
            "SNAP-N",
        )
        self.assertEqual(
            result.generated.files[0].relative_path,
            Path("gamedata/configs/new.ltx"),
        )
        self.assertEqual(result.generated.files[0].text, text)
        self.assertEqual(engine.preview(result.plan), text)

        regenerated = engine.generate(result.plan)
        self.assertEqual(regenerated.files[0].text, text)
        self.assertTrue(engine.verify(regenerated).ok)

    def test_new_xml_file_uses_xml_format(self):
        intent = PatchIntent(
            kind="new_file",
            source_path=Path("new.xml"),
            after_text="<x/>",
            relative_path=Path("new.xml"),
        )
        result = NewFilePatchEngine().plan(
            intent,
            PatchSnapshot(snapshot_id="SNAP-N"),
        )
        self.assertEqual(result.plan.format, "xml")
        self.assertEqual(result.plan.snapshot_id, "SNAP-N")
        self.assertEqual(
            result.generated.plan.snapshot_id,
            "SNAP-N",
        )

    def test_snapshot_mismatch_is_rejected(self):
        intent = PatchIntent(
            kind="new_file",
            source_path=Path("new.ltx"),
            options=PatchOptions(snapshot_id="OTHER"),
            after_text="[a]\nx = 1\n",
            relative_path=Path("new.ltx"),
        )
        with self.assertRaises(PatchPlanningError):
            NewFilePatchEngine().plan(
                intent,
                PatchSnapshot(snapshot_id="SNAP-N"),
            )

    def test_options_snapshot_id_is_used_when_snapshot_is_empty(self):
        intent = PatchIntent(
            kind="new_file",
            source_path=Path("new.ltx"),
            options=PatchOptions(snapshot_id="LEGACY"),
            after_text="[a]\nx = 1\n",
            relative_path=Path("new.ltx"),
        )
        result = NewFilePatchEngine().plan(
            intent,
            PatchSnapshot(),
        )
        self.assertEqual(result.plan.snapshot_id, "LEGACY")
        self.assertEqual(
            result.generated.plan.snapshot_id,
            "LEGACY",
        )

    def test_rejects_unsupported_suffix_and_empty_text(self):
        engine = NewFilePatchEngine()
        for source, text, relative in (
            (Path("new.txt"), "x", Path("new.txt")),
            (Path("new.ltx"), "  ", Path("new.ltx")),
        ):
            with self.subTest(source=source):
                intent = PatchIntent(
                    kind="new_file",
                    source_path=source,
                    after_text=text,
                    relative_path=relative,
                )
                with self.assertRaises(PatchPlanningError):
                    engine.plan(intent, PatchSnapshot())


class PatchBundleTest(unittest.TestCase):
    def test_empty_bundle_is_not_ok(self):
        intent = PatchIntent(kind="ltx", source_path=Path("a.ltx"))
        plan = PatchPlan(
            source_path=Path("a.ltx"),
            target=PatchTarget(
                source_path=Path("a.ltx"),
                root_path=None,
            ),
            patch_mod="Test",
            patch_file_rel=None,
        )
        bundle = PatchBundle(
            intent=intent,
            snapshot=PatchSnapshot(),
            plan=plan,
        )
        self.assertFalse(bundle.ok)
        self.assertEqual(bundle.notes, ())

    def test_bundle_with_plan_error_is_not_ok(self):
        intent = PatchIntent(kind="ltx", source_path=Path("a.ltx"))
        plan = PatchPlan(
            source_path=Path("a.ltx"),
            target=PatchTarget(
                source_path=Path("a.ltx"),
                root_path=None,
            ),
            patch_mod="Test",
            patch_file_rel=Path("mod_a.ltx"),
            issues=(
                PatchIssue(
                    code="test_error",
                    message="Ошибка плана",
                    severity="error",
                ),
            ),
        )
        generated = GeneratedPatch(
            plan=plan,
            files=(
                PatchFile(
                    relative_path=Path("gamedata/configs/mod_a.ltx"),
                    text="[a]\nx = 1\n",
                ),
            ),
            text="[a]\nx = 1\n",
            verification=VerificationResult(ok=True),
        )
        bundle = PatchBundle(
            intent=intent,
            snapshot=PatchSnapshot(),
            plan=plan,
            generated=generated,
            verification=generated.verification,
        )
        self.assertFalse(bundle.ok)

    def test_bundle_without_files_is_not_ok(self):
        intent = PatchIntent(kind="ltx", source_path=Path("a.ltx"))
        plan = PatchPlan(
            source_path=Path("a.ltx"),
            target=PatchTarget(
                source_path=Path("a.ltx"),
                root_path=None,
            ),
            patch_mod="Test",
            patch_file_rel=Path("mod_a.ltx"),
        )
        generated = GeneratedPatch(
            plan=plan,
            files=(),
            text="",
            verification=VerificationResult(ok=True),
        )
        bundle = PatchBundle(
            intent=intent,
            snapshot=PatchSnapshot(),
            plan=plan,
            generated=generated,
            verification=generated.verification,
        )
        self.assertFalse(bundle.ok)

    def test_bundle_is_frozen(self):
        intent = PatchIntent(kind="ltx", source_path=Path("a.ltx"))
        plan = PatchPlan(
            source_path=Path("a.ltx"),
            target=PatchTarget(
                source_path=Path("a.ltx"),
                root_path=None,
            ),
            patch_mod="Test",
            patch_file_rel=None,
        )
        bundle = PatchBundle(
            intent=intent,
            snapshot=PatchSnapshot(),
            plan=plan,
        )
        with self.assertRaises(FrozenInstanceError):
            bundle.notes = ("x",)


if __name__ == "__main__":
    unittest.main()
