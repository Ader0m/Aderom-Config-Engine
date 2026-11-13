# -*- coding: utf-8 -*-
"""Режим On-file: отдельный DLTX-патч на каждый изменённый файл.

Все патчи привязываются к корню загрузки, но не сливаются в один файл:
у каждого изменённого документа свой ``mod_<root>_<suffix>.ltx``.
"""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.patching.ltx.generator import DltxGenerator
from core.patching.ltx.verifier import ModelPatchVerifier
from core.patching.planner import PatchPlanner
from ui.project import ProjectService


class Forest:
    def __init__(self, config_dir, files, parents=None):
        self.scan_dir = str(config_dir)
        self.files = list(files)
        self.xml_files = []
        self.include_parents = dict(parents or {})
        self.rel_paths = {
            path: str(path.relative_to(config_dir)).replace("/", "\\")
            for path in files
        }


class Services:
    def __init__(self):
        self.patch = PatchPlanner()

    def generate_patch(self, plan):
        generated = DltxGenerator().generate(plan)
        return replace(
            generated,
            verification=ModelPatchVerifier().verify(generated),
        )


class OnFileModeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name) / "configs"
        self.config.mkdir(parents=True)

        self.root = self.config / "system.ltx"
        self.first = self.config / "first.ltx"
        self.second = self.config / "second.ltx"

        self.root.write_bytes(
            b'[system]\n#include "first.ltx"\n#include "second.ltx"\n'
        )
        self.first.write_bytes(b"[one]\na = old\n")
        self.second.write_bytes(b"[two]\nb = old\nc = old\nd = old\n")

        self.forest = Forest(
            self.config,
            [self.root, self.first, self.second],
            {
                self.first: {self.root},
                self.second: {self.root},
            },
        )

    def test_one_patch_per_changed_file_for_common_root(self):
        projects = ProjectService(Path(self.temp.name) / "projects")
        projects.create("onfile", save_mode="on_file")
        projects.update_draft(self.first, "[one]\na = new\n")
        projects.update_draft(
            self.second,
            "[two]\nb = new\nc = new\nd = new\n",
        )

        preview = projects.build_preview(Services(), self.forest)

        self.assertEqual(len(preview.items), 2)
        self.assertTrue(all(item.ok for item in preview.items))

        # Оба патча адресованы корню system.ltx, а не include-файлам.
        for item in preview.items:
            self.assertEqual(item.plan.target.root_path, self.root)
            self.assertEqual(len(item.generated.files), 1)

        names = {
            item.generated.files[0].relative_path.name
            for item in preview.items
        }
        self.assertEqual(len(names), 2)
        self.assertTrue(all(name.startswith("mod_system_") for name in names))
        self.assertFalse(any("mod_first_" in name for name in names))
        self.assertFalse(any("mod_second_" in name for name in names))

        texts = {
            item.generated.files[0].relative_path.name:
                item.generated.files[0].text
            for item in preview.items
        }
        first_text = next(
            text for name, text in texts.items() if "a = new" in text
        )
        second_text = next(
            text for name, text in texts.items() if "b = new" in text
        )

        self.assertIn("a = new", first_text)
        self.assertNotIn("b = new", first_text)
        self.assertIn("b = new", second_text)
        self.assertIn("c = new", second_text)
        self.assertIn("d = new", second_text)
        self.assertNotIn("a = new", second_text)

    def test_legacy_in_place_project_loads_as_on_file(self):
        projects = ProjectService(Path(self.temp.name) / "legacy")
        project = projects.create("legacy", save_mode="in_place")
        self.assertEqual(project.save_mode, "on_file")
        project_id = project.id
        projects.close()

        loaded = projects.load(project_id)
        self.assertEqual(loaded.save_mode, "on_file")


if __name__ == "__main__":
    unittest.main()
