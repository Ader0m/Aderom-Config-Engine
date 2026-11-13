# -*- coding: utf-8 -*-
"""Сырой просмотр LTX: шапка и переход к строке."""

import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from modules.base_module.ltx_open import LtxFileOpener


class _Session:
    has_tree = True
    forest = object()
    config_dir = None


class _Services:
    def __init__(self, path):
        self.path = path

    def read_text(self, path):
        return Path(path).read_text(encoding="utf-8")

    def relative_path(self, _forest, path):
        return Path(path).name

    def patch_target(self, _forest, _path):
        return Path("root.ltx")


class _Ui:
    def __init__(self):
        self.text = ""
        self.label = ""
        self.notice = ""
        self.line = None

    def show_text(self, text, label, readonly=True, source_path=None,
                  line_number=None, syntax=True, notice=None):
        self.text = text
        self.label = label
        self.notice = notice or ""
        self.line = line_number

    def set_status(self, *_args, **_kwargs):
        pass

    def log(self, *_args, **_kwargs):
        pass

    def show_error(self, message):
        raise AssertionError(message)


class _Ctx:
    def __init__(self, path):
        self.ui = _Ui()
        self.services = _Services(path)


class LtxRawOpenTest(unittest.TestCase):
    def _open(self, name, line_number):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / name
            path.write_text(
                "[section]\nfirst=1\nsecond=2\n",
                encoding="utf-8",
            )
            ctx = _Ctx(path)
            opener = LtxFileOpener(ctx, _Session())
            self.assertTrue(opener.open_raw(path, line_number))
            lines = ctx.ui.text.splitlines()
            self.assertEqual(
                lines[ctx.ui.line - 1],
                "first=1",
            )
            return ctx.ui

    def test_raw_file_line_accounts_for_header(self):
        ui = self._open("items.ltx", 2)
        self.assertEqual(ui.label, "[Raw]items.ltx")
        self.assertIn("СЫРОЙ ФАЙЛ", ui.notice)

    def test_patch_file_line_accounts_for_header(self):
        ui = self._open("mod_items_patch.ltx", 2)
        self.assertTrue(ui.label.startswith("DLTX PATCH:"))
        self.assertIn("DLTX-ПАТЧ", ui.notice)


if __name__ == "__main__":
    unittest.main()
