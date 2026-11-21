# -*- coding: utf-8 -*-
"""XML-цели патча должны учитывать include-черновики проекта."""

import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401
from tests.helpers import FakeForest, write_text

from core.patching.models import PatchOptions
from core.patching.targets import xml_root_candidates
from core.patching.xml.service import XmlPatchService
from sdk import ProjectOverlay


class XmlTargetDraftTest(unittest.TestCase):
    def _setup(self, directory):
        cfg = Path(directory) / "gamedata" / "configs"
        root = write_text(
            cfg / "ui" / "root.xml",
            '<root>\n#include "ui\\child.xml"\n</root>\n',
        )
        child = write_text(
            cfg / "ui" / "child.xml",
            "<child/>\n",
        )
        other = write_text(
            cfg / "ui" / "other.xml",
            "<other/>\n",
        )
        forest = FakeForest(
            cfg,
            [],
            xml_files=[root, child, other],
        )
        return forest, root, child, other

    def test_draft_include_changes_target(self):
        with tempfile.TemporaryDirectory() as d:
            forest, root, child, other = self._setup(d)

            # Прогреваем дисковый индекс.
            self.assertEqual(
                xml_root_candidates(forest, child),
                (root,),
            )

            overlay = ProjectOverlay(
                source_files={
                    root: '<root>\n#include "ui\\other.xml"\n</root>\n',
                },
            )

            self.assertEqual(
                xml_root_candidates(forest, child, overlay=overlay),
                (child,),
            )
            self.assertEqual(
                xml_root_candidates(forest, other, overlay=overlay),
                (root,),
            )

    def test_overlay_change_invalidates_cached_index(self):
        with tempfile.TemporaryDirectory() as d:
            forest, root, child, other = self._setup(d)

            first = ProjectOverlay(
                source_files={
                    root: '<root>\n#include "ui\\other.xml"\n</root>\n',
                },
            )
            second = ProjectOverlay(
                source_files={
                    root: '<root>\n#include "ui\\child.xml"\n</root>\n',
                },
            )

            self.assertEqual(
                xml_root_candidates(forest, child, overlay=first),
                (child,),
            )
            self.assertEqual(
                xml_root_candidates(forest, other, overlay=first),
                (root,),
            )
            self.assertEqual(
                xml_root_candidates(forest, child, overlay=second),
                (root,),
            )
            self.assertEqual(
                xml_root_candidates(forest, other, overlay=second),
                (other,),
            )

    def test_xml_patch_service_passes_overlay_to_target(self):
        with tempfile.TemporaryDirectory() as d:
            forest, root, child, _other = self._setup(d)
            overlay = ProjectOverlay(
                source_files={
                    root: '<root>\n#include "ui\\child.xml"\n</root>\n',
                },
            )
            before = "<child/>\n"
            after = "<child>\n  <value>1</value>\n</child>\n"
            options = PatchOptions(xml_patch_mode="dxml_lua")

            result = XmlPatchService().plan(
                child,
                before,
                after,
                mode="dxml_lua",
                forest=forest,
                options=options,
                overlay=overlay,
            )

            self.assertEqual(result.plan.target.root_path, root)
            self.assertEqual(result.plan.target.candidates, (root,))

    def test_new_virtual_xml_is_included_by_draft(self):
        with tempfile.TemporaryDirectory() as d:
            forest, root, _child, _other = self._setup(d)
            new_child = Path(forest.scan_dir) / "ui" / "new_child.xml"

            overlay = ProjectOverlay(
                source_files={
                    root: '<root>\n#include "ui\\new_child.xml"\n</root>\n',
                },
                new_files=frozenset({new_child}),
            )

            self.assertEqual(
                xml_root_candidates(forest, new_child, overlay=overlay),
                (root,),
            )


if __name__ == "__main__":
    unittest.main()
