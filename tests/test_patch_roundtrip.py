# -*- coding: utf-8 -*-
"""Полный round-trip затронутого LTX-корня."""

import unittest
from dataclasses import dataclass
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.patching.models import (
    GeneratedPatch,
    PatchFile,
    PatchPlan,
    PatchTarget,
    VerificationResult,
)
from core.patching.roundtrip import PatchRoundTripVerifier


@dataclass(frozen=True)
class _Change:
    kind: str
    section: str
    key: str = ""
    new_value: str = ""
    parent: str = ""
    risk: str = "safe"


class DLTXToken:
    name = "DELETE"


class _Adapter:
    def __init__(
        self,
        baseline,
        patched,
        sources=None,
        duplicates=None,
        parents=None,
        patched_parents=None,
    ):
        self.baseline = baseline
        self.patched = patched
        self.sources = sources or {}
        self.duplicates = duplicates or {}
        self.parents = parents or {}
        self.patched_parents = (
            self.parents
            if patched_parents is None
            else patched_parents
        )
        self.patch_files = {}

    def resolve_sections_details(self, root_path):
        return self.baseline, self.parents

    def resolve_with_virtual_files_details(self, root_path, patch_files):
        self.patch_files = dict(patch_files)
        return (
            self.patched,
            self.sources,
            self.duplicates,
            self.patched_parents,
        )


def _generated(change=None, patch_text="[w]\ncost = 150\n"):
    source = Path("C:/game/gamedata/configs/a.ltx")
    plan = PatchPlan(
        source_path=source,
        target=PatchTarget(
            source_path=source,
            root_path=Path("C:/game/gamedata/configs/system.ltx"),
        ),
        patch_mod="Test",
        patch_file_rel=Path("mod_a.ltx"),
        changes=(change,) if change is not None else (),
        snapshot_id="SNAP",
    )
    return GeneratedPatch(
        plan=plan,
        files=(
            PatchFile(
                relative_path=Path("gamedata/configs/mod_a.ltx"),
                text=patch_text,
            ),
        ),
        text=patch_text,
        verification=VerificationResult(ok=True),
    )


class PatchRoundTripVerifierTest(unittest.TestCase):
    def _change(self):
        return _Change(
            kind="set_key",
            section="w",
            key="cost",
            new_value="150",
        )

    def test_success_checks_full_diff_and_source(self):
        adapter = _Adapter(
            {"w": {"cost": "100", "keep": "x"}},
            {"w": {"cost": "150", "keep": "x"}},
            {"w": {"cost": "mod_a", "keep": "a"}},
        )
        report = PatchRoundTripVerifier(adapter=adapter).verify(
            _generated(self._change()),
        )

        self.assertTrue(report.ok, report.issues)
        self.assertIn(
            "full_diff",
            [check.name for check in report.checks],
        )
        self.assertIn("source", [check.name for check in report.checks])

    def test_inherited_key_removal_is_explained(self):
        change = _Change(
            kind="delete_key",
            section="base",
            key="x",
        )
        adapter = _Adapter(
            {
                "base": {"x": "1"},
                "weapon": {"x": "1", "y": "2"},
            },
            {
                "base": {},
                "weapon": {"y": "2"},
            },
            {"weapon": {"y": "mod_a"}},
            parents={"weapon": ("base",)},
        )
        report = PatchRoundTripVerifier(adapter=adapter).verify(
            _generated(change, patch_text="[base]\n!x\n"),
        )

        self.assertTrue(report.ok, report.issues)

    def test_parent_removal_is_explained(self):
        change = _Change(
            kind="remove_parent",
            section="weapon",
            parent="base",
        )
        adapter = _Adapter(
            {
                "base": {"x": "1"},
                "weapon": {"x": "1", "y": "2"},
            },
            {
                "base": {"x": "1"},
                "weapon": {"y": "2"},
            },
            {"weapon": {"y": "mod_a"}},
            parents={"weapon": ("base",)},
            patched_parents={"weapon": ()},
        )
        report = PatchRoundTripVerifier(adapter=adapter).verify(
            _generated(change, patch_text="[weapon]:!base\ny = 2\n"),
        )

        self.assertTrue(report.ok, report.issues)

    def test_planned_parent_change_must_match(self):
        change = _Change(
            kind="remove_parent",
            section="weapon",
            parent="base",
        )
        adapter = _Adapter(
            {
                "base": {"x": "1"},
                "weapon": {"x": "1", "y": "2"},
            },
            {
                "base": {"x": "1"},
                "weapon": {"y": "2"},
            },
            {"weapon": {"y": "mod_a"}},
            parents={"weapon": ("base",)},
            patched_parents={"weapon": ("base",)},
        )
        report = PatchRoundTripVerifier(adapter=adapter).verify(
            _generated(change, patch_text="[weapon]:!base\ny = 2\n"),
        )

        self.assertFalse(report.ok)
        self.assertIn(
            "roundtrip_parents_mismatch",
            [issue.code for issue in report.issues],
        )

    def test_intermediate_parent_removal_is_explained(self):
        change = _Change(
            kind="remove_parent",
            section="base",
            parent="root",
        )
        adapter = _Adapter(
            {
                "root": {"x": "1"},
                "base": {"x": "1"},
                "weapon": {"x": "1", "y": "2"},
            },
            {
                "root": {"x": "1"},
                "base": {},
                "weapon": {"y": "2"},
            },
            {"weapon": {"y": "mod_a"}},
            parents={
                "base": ("root",),
                "weapon": ("base",),
            },
            patched_parents={
                "base": (),
                "weapon": ("base",),
            },
        )
        report = PatchRoundTripVerifier(adapter=adapter).verify(
            _generated(
                change,
                patch_text="[base]\n!root\n",
            ),
        )

        self.assertTrue(report.ok, report.issues)

    def test_lost_key_is_an_error(self):
        adapter = _Adapter(
            {"w": {"cost": "100", "keep": "x"}},
            {"w": {"cost": "150"}},
            {"w": {"cost": "mod_a"}},
        )
        report = PatchRoundTripVerifier(adapter=adapter).verify(
            _generated(self._change()),
        )

        self.assertFalse(report.ok)
        self.assertIn(
            "roundtrip_key_lost",
            [issue.code for issue in report.issues],
        )

    def test_duplicate_keys_are_an_error(self):
        adapter = _Adapter(
            {"w": {"cost": "100"}},
            {"w": {"cost": "150"}},
            {"w": {"cost": "mod_a"}},
            {"w": ("cost",)},
        )
        report = PatchRoundTripVerifier(adapter=adapter).verify(
            _generated(self._change()),
        )

        self.assertFalse(report.ok)
        self.assertIn(
            "roundtrip_duplicate_key",
            [issue.code for issue in report.issues],
        )

    def test_dltx_artifact_is_an_error(self):
        adapter = _Adapter(
            {"w": {"cost": "100"}},
            {"w": {"cost": DLTXToken()}},
            {"w": {"cost": "mod_a"}},
        )
        report = PatchRoundTripVerifier(adapter=adapter).verify(
            _generated(self._change()),
        )

        self.assertFalse(report.ok)
        self.assertIn(
            "roundtrip_dltx_artifact",
            [issue.code for issue in report.issues],
        )

    def test_source_must_be_patch_file(self):
        adapter = _Adapter(
            {"w": {"cost": "100"}},
            {"w": {"cost": "150"}},
            {"w": {"cost": "other_mod"}},
        )
        report = PatchRoundTripVerifier(adapter=adapter).verify(
            _generated(self._change()),
        )

        self.assertFalse(report.ok)
        self.assertIn(
            "roundtrip_source_mismatch",
            [issue.code for issue in report.issues],
        )

    def test_value_mismatch_is_an_error(self):
        adapter = _Adapter(
            {"w": {"cost": "100"}},
            {"w": {"cost": "160"}},
            {"w": {"cost": "mod_a"}},
        )
        report = PatchRoundTripVerifier(adapter=adapter).verify(
            _generated(self._change()),
        )

        self.assertFalse(report.ok)
        self.assertIn(
            "roundtrip_value_mismatch",
            [issue.code for issue in report.issues],
        )

    def test_all_patch_files_are_virtualized_at_their_paths(self):
        source = Path("C:/game/gamedata/configs/a.ltx")
        plan = PatchPlan(
            source_path=source,
            target=PatchTarget(
                source_path=source,
                root_path=Path("C:/game/gamedata/configs/system.ltx"),
            ),
            patch_mod="Test",
            patch_file_rel=Path("defines.ltx"),
            changes=(),
            snapshot_id="SNAP",
            format="ltx_override",
        )
        generated = GeneratedPatch(
            plan=plan,
            files=(
                PatchFile(
                    relative_path=Path("gamedata/configs/defines.ltx"),
                    text="[hud_base]\nvalue = 1\n",
                ),
                PatchFile(
                    relative_path=Path(
                        "gamedata/configs/mod_system_mas_base.ltx"
                    ),
                    text="[scope_base]\nvalue = 2\n",
                ),
            ),
            text="; combined preview",
            verification=VerificationResult(ok=True),
        )

        adapter = _Adapter({}, {})
        report = PatchRoundTripVerifier(adapter=adapter).verify(generated)

        self.assertTrue(report.ok, report.issues)
        self.assertEqual(
            set(adapter.patch_files),
            {
                Path("C:/game/gamedata/configs/defines.ltx"),
                Path("C:/game/gamedata/configs/mod_system_mas_base.ltx"),
            },
        )

    def test_xml_is_verified_by_xml_verifier(self):
        generated = _generated(self._change())
        generated = GeneratedPatch(
            plan=PatchPlan(
                source_path=generated.plan.source_path,
                target=generated.plan.target,
                patch_mod=generated.plan.patch_mod,
                patch_file_rel=generated.plan.patch_file_rel,
                changes=generated.plan.changes,
                snapshot_id=generated.plan.snapshot_id,
                format="xml",
            ),
            files=generated.files,
            text=generated.text,
            verification=VerificationResult(ok=True),
        )
        report = PatchRoundTripVerifier().verify(generated)
        self.assertTrue(report.ok)
        self.assertEqual(report.checks[0].name, "roundtrip")


if __name__ == "__main__":
    unittest.main()
