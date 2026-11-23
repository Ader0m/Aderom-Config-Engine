# -*- coding: utf-8 -*-
"""Сборка XML-патча проекта в core (шаг 5.2)."""

import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.patching.models import (
    GeneratedPatch,
    PatchFile,
    PatchPlan,
    PatchPlanningError,
    PatchTarget,
    VerificationResult,
)
from core.patching.xml.merge import merge_xml_generated


def _plan(source, mode, changes=None):
    source = Path(source)
    return PatchPlan(
        source_path=source,
        target=PatchTarget(source_path=source, root_path=None),
        patch_mod="Test",
        patch_file_rel=Path(source.name),
        changes=(object(),) if changes is None else tuple(changes),
        xml_mode=mode,
        format="xml",
    )


def _generated(plan, files):
    return GeneratedPatch(
        plan=plan,
        files=tuple(files),
        text="",
        verification=VerificationResult(ok=True),
    )


class _Services:
    def __init__(self, generated):
        self.generated = generated
        self.calls = []

    def generate_patch(self, plan):
        self.calls.append(plan)
        return self.generated[plan]


class MergeXmlGeneratedTest(unittest.TestCase):
    def test_merges_files_and_filters_dltx(self):
        first = _plan("a.xml", "full_copy")
        second = _plan("b.xml", "dxml_lua")
        services = _Services(
            {
                first: _generated(
                    first,
                    (
                        PatchFile(
                            relative_path=Path("gamedata/configs/a.xml"),
                            text="<a/>",
                        ),
                        PatchFile(
                            relative_path=Path("gamedata/configs/mod_a.ltx"),
                            text="![x]\n",
                        ),
                    ),
                ),
                second: _generated(
                    second,
                    (
                        PatchFile(
                            relative_path=Path("gamedata/configs/b.xml"),
                            text="<b/>",
                        ),
                    ),
                ),
            }
        )

        merged, generated = merge_xml_generated(
            services,
            (
                (first, None, first.source_path),
                (second, None, second.source_path),
            ),
        )

        self.assertEqual(len(merged.changes), 2)
        self.assertEqual(len(services.calls), 2)
        self.assertEqual(
            [file.relative_path.name for file in generated.files],
            ["a.xml", "b.xml"],
        )
        self.assertTrue(generated.verification.ok)
        self.assertIn("-- Файл: gamedata", generated.text)
        self.assertIn("<a/>", generated.text)
        self.assertIn("<b/>", generated.text)

    def test_mode_conflict_is_an_error(self):
        first = _plan("a.xml", "full_copy")
        second = _plan("a.xml", "dxml_lua")
        services = _Services(
            {
                first: _generated(
                    first,
                    (
                        PatchFile(
                            relative_path=Path("gamedata/configs/a.xml"),
                            text="<a/>",
                        ),
                    ),
                ),
                second: _generated(
                    second,
                    (
                        PatchFile(
                            relative_path=Path("gamedata/configs/b.xml"),
                            text="<b/>",
                        ),
                    ),
                ),
            }
        )

        _merged, generated = merge_xml_generated(
            services,
            (
                (first, None, first.source_path),
                (second, None, Path("a.xml")),
            ),
        )

        self.assertFalse(generated.verification.ok)
        self.assertIn(
            "xml_mode_conflict",
            [issue.code for issue in generated.verification.issues],
        )

    def test_pre_generated_fork_is_used_without_regeneration(self):
        plan = _plan("a.xml", "auto")
        plan = PatchPlan(
            source_path=plan.source_path,
            target=plan.target,
            patch_mod=plan.patch_mod,
            patch_file_rel=plan.patch_file_rel,
            changes=plan.changes,
            xml_mode=plan.xml_mode,
            format="xml_fork",
        )
        pre_generated = _generated(
            plan,
            (
                PatchFile(
                    relative_path=Path("gamedata/configs/a_ace.xml"),
                    text="<a/>",
                ),
            ),
        )
        services = _Services({})

        _merged, generated = merge_xml_generated(
            services,
            ((plan, pre_generated, plan.source_path),),
        )

        self.assertEqual(services.calls, [])
        self.assertEqual(
            generated.files[0].relative_path.name,
            "a_ace.xml",
        )
        self.assertTrue(generated.verification.ok)

    def test_empty_entries_is_rejected(self):
        with self.assertRaises(PatchPlanningError):
            merge_xml_generated(_Services({}), ())


if __name__ == "__main__":
    unittest.main()
