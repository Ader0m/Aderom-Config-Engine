# -*- coding: utf-8 -*-
"""Сырой просмотр LTX: шапка и переход к строке."""

import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from modules.base.ltx_open import LtxFileOpener


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
        self.readonly = None
        self.tab_id = None
        self.tab_ids = []
        self.logs = []

    def show_text(self, text, label, readonly=True, source_path=None,
                  line_number=None, syntax=True, notice=None, tab_id=None):
        self.text = text
        self.label = label
        self.notice = notice or ""
        self.line = line_number
        self.readonly = readonly
        self.tab_id = tab_id
        self.tab_ids.append(tab_id)

    def set_status(self, *_args, **_kwargs):
        pass

    def log(self, message, *_args, **_kwargs):
        self.logs.append(str(message))

    def show_error(self, message):
        raise AssertionError(message)


class _FocusUi(_Ui):
    """UI с одной уже открытой resolved-вкладкой."""

    def __init__(self, open_tab_id):
        super().__init__()
        self.open_tab_id = str(open_tab_id)
        self.focused = []
        self.activated = []

    def request_tab(self, tab_id):
        self.focused.append(str(tab_id))
        return str(tab_id) == self.open_tab_id

    def open_resolved_document(self, tab_id, line_number=None):
        self.activated.append((str(tab_id), line_number))
        return True


class _Documents:
    """Project text API для сырого просмотра LTX."""

    def read_project_text(self, path):
        path = Path(path)
        if path.is_file():
            return path.read_text(encoding="utf-8")
        return ""


class _Ctx:
    def __init__(self, path):
        self.ui = _Ui()
        self.documents = _Documents()
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
        self.assertEqual(ui.tab_id, "base.raw:items.ltx")
        self.assertIn("СЫРОЙ ФАЙЛ", ui.notice)
        self.assertTrue(ui.readonly)

    def test_patch_file_line_accounts_for_header(self):
        ui = self._open("mod_items_patch.ltx", 2)
        self.assertTrue(ui.label.startswith("DLTX PATCH:"))
        self.assertEqual(ui.tab_id, "base.raw:mod_items_patch.ltx")
        self.assertIn("DLTX-ПАТЧ", ui.notice)
        self.assertFalse(ui.readonly)

    def test_two_patches_get_different_tab_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "mod_items_a.ltx"
            second = Path(directory) / "mod_items_b.ltx"
            first.write_text("[a]\nx = 1\n", encoding="utf-8")
            second.write_text("[b]\ny = 2\n", encoding="utf-8")

            ctx = _Ctx(first)
            opener = LtxFileOpener(ctx, _Session())
            self.assertTrue(opener.open_raw(first))
            self.assertTrue(opener.open_raw(second))

            self.assertEqual(
                ctx.ui.tab_ids,
                ["base.raw:mod_items_a.ltx", "base.raw:mod_items_b.ltx"],
            )


class LtxAlreadyOpenTest(unittest.TestCase):
    def _opener(self, tab_id):
        path = Path("configs") / "evaluation.ltx"
        ctx = _Ctx(path)
        ctx.ui = _FocusUi(tab_id)

        class Tasks:
            def run(self, *_args, **_kwargs):
                raise AssertionError("повторный резолв не должен запускаться")

        ctx.tasks = Tasks()
        return ctx, LtxFileOpener(ctx, _Session()), path

    def test_open_existing_resolved_tab_skips_resolution(self):
        tab_id = "base.resolved:evaluation.ltx"
        ctx, opener, path = self._opener(tab_id)

        self.assertTrue(opener.open(path))

        self.assertEqual(ctx.ui.focused, [tab_id])
        self.assertEqual(ctx.ui.activated, [(tab_id, None)])
        self.assertIn("Файл уже открыт", "\n".join(ctx.ui.logs))

    def test_open_existing_resolved_tab_keeps_line_navigation(self):
        tab_id = "base.resolved:evaluation.ltx"
        ctx, opener, path = self._opener(tab_id)

        self.assertTrue(opener.open_resolved_at_line(path, 42))

        self.assertEqual(ctx.ui.activated, [(tab_id, 42)])


if __name__ == "__main__":
    unittest.main()
