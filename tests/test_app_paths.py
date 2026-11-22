# -*- coding: utf-8 -*-
"""Portable-раскладка данных приложения."""

import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from main import _application_root, _migrate_generated_dir


class PortablePathsTest(unittest.TestCase):
    def test_source_layout_uses_project_root(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "ACE"
            src = root / "src"
            src.mkdir(parents=True)

            self.assertEqual(_application_root(src), root.resolve())

    def test_generated_moves_from_src_once(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "ACE"
            old = root / "src" / "generated"
            old.mkdir(parents=True)
            (old / "cache.bin").write_bytes(b"cache")
            new = root / "generated"

            _migrate_generated_dir(old, new)

            self.assertFalse(old.exists())
            self.assertEqual(
                (new / "cache.bin").read_bytes(),
                b"cache",
            )

    def test_existing_portable_generated_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "ACE"
            old = root / "src" / "generated"
            old.mkdir(parents=True)
            (old / "old.bin").write_bytes(b"old")
            new = root / "generated"
            new.mkdir(parents=True)
            (new / "new.bin").write_bytes(b"new")

            _migrate_generated_dir(old, new)

            self.assertTrue(old.exists())
            self.assertEqual((new / "new.bin").read_bytes(), b"new")


if __name__ == "__main__":
    unittest.main()
