# -*- coding: utf-8 -*-
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.tools.vfs import clear_cache, resolve_physical_path


class VfsPathsTest(unittest.TestCase):
    def setUp(self):
        clear_cache()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        clear_cache()
        self.tmp.cleanup()

    def test_regular_file_resolves_to_itself(self):
        path = self.root / "texture.dds"
        path.write_bytes(b"DDS ")

        resolved = resolve_physical_path(path)

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.resolve(), path.resolve())

    def test_missing_file_returns_none(self):
        self.assertIsNone(
            resolve_physical_path(self.root / "missing.dds")
        )

    def test_materialize_creates_real_copy(self):
        path = self.root / "ui" / "icon.dds"
        path.parent.mkdir()
        path.write_bytes(b"DDS content")

        resolved = resolve_physical_path(path, materialize=True)

        self.assertIsNotNone(resolved)
        self.assertNotEqual(
            str(resolved).lower(),
            str(path).lower(),
        )
        self.assertEqual(resolved.name, "icon.dds")
        self.assertEqual(resolved.read_bytes(), b"DDS content")
        self.assertTrue(resolved.is_file())
        self.assertEqual(path.read_bytes(), b"DDS content")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink недоступен")
    def test_symlink_resolves_to_target(self):
        target = self.root / "real.dds"
        target.write_bytes(b"DDS ")
        link = self.root / "link.dds"
        try:
            link.symlink_to(target)
        except OSError as exc:
            self.skipTest("нет прав на создание symlink: %s" % exc)

        resolved = resolve_physical_path(link)

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.resolve(), target.resolve())

    @unittest.skipUnless(os.name == "nt", "junction только в Windows")
    def test_junction_resolves_to_target(self):
        real = self.root / "real"
        real.mkdir()
        target = real / "icon.dds"
        target.write_bytes(b"DDS ")
        link = self.root / "link"

        try:
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(real)],
                check=True,
                capture_output=True,
            )
        except Exception as exc:
            self.skipTest("не удалось создать junction: %s" % exc)

        resolved = resolve_physical_path(link / "icon.dds")

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.resolve(), target.resolve())


if __name__ == "__main__":
    unittest.main()
