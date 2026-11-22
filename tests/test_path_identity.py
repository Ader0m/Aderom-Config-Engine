# -*- coding: utf-8 -*-
"""Единая идентичность путей и адаптер ctx.paths."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from app.paths import PathsAdapter
from core.api.paths import path_key, same_path
from sdk.contracts.paths import AppPaths


class PathIdentityTest(unittest.TestCase):
    def test_case_and_dot_segments_are_normalized(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            nested = root / "configs" / "sub" / ".." / "system.ltx"
            self.assertEqual(
                path_key(nested),
                path_key(root / "configs" / "system.ltx"),
            )
            self.assertTrue(
                same_path(
                    root / "configs" / "SYSTEM.LTX",
                    root / "configs" / "system.ltx",
                )
            )

    def test_paths_adapter_delegates_to_core(self):
        root = Path.cwd()
        adapter = PathsAdapter(
            AppPaths(
                app_dir=root,
                generated_dir=root / "generated",
                projects_dir=root / "projects",
            )
        )
        self.assertEqual(
            adapter.key(root / "a" / ".." / "b.ltx"),
            path_key(root / "b.ltx"),
        )
        self.assertTrue(
            adapter.same(root / "A.LTX", root / "a.ltx")
        )

    def test_junction_is_not_collapsed_to_target(self):
        if os.name != "nt":
            self.skipTest("junction проверяется только на Windows")

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            target = root / "real"
            target.mkdir()
            link = root / "link"
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0 or not link.exists():
                self.skipTest("не удалось создать junction")

            # Каноническая идентичность VFS не разыменовывает ссылки:
            # иначе путь MO2 подменился бы путём мода.
            self.assertNotEqual(path_key(link), path_key(target))
            self.assertFalse(same_path(link, target))


if __name__ == "__main__":
    unittest.main()
