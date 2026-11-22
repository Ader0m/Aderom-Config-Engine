# -*- coding: utf-8 -*-
"""Открытие XML: ошибки резолва и request_id."""

import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from modules.base_module.xml_open import XmlFileOpener


class _Session:
    has_tree = True
    config_dir = Path("configs")
    forest = None


class _Services:
    def relative_path(self, _forest, path):
        return Path(path).name


class _Ui:
    def __init__(self):
        self.failed = []
        self.opened = []
        self.error = ""
        self.logs = []
        self.focused = []
        self.activated = []

    def notify_file_open_failed(self, path, reason="", request_id=None):
        self.failed.append((Path(path), reason, request_id))

    def open_xml_resolved_tab(self, *args, **kwargs):
        self.opened.append((args, kwargs))

    def set_status(self, *_args, **_kwargs):
        pass

    def log(self, message, *_args, **_kwargs):
        self.logs.append(str(message))

    def show_error(self, text):
        self.error = str(text)


class _FocusUi(_Ui):
    def __init__(self, open_tab_id):
        super().__init__()
        self.open_tab_id = str(open_tab_id)

    def request_tab(self, tab_id):
        self.focused.append(str(tab_id))
        return str(tab_id) == self.open_tab_id

    def open_resolved_document(self, tab_id, line_number=None):
        self.activated.append((str(tab_id), line_number))
        return True


class _Ctx:
    def __init__(self):
        self.ui = _Ui()
        self.services = _Services()
        self.documents = self.ui
        self.items = self.ui
        self.projects = self.ui


class _Result:
    text = "[resolved]\n"
    dxml_applied = False
    notes = ()
    line_sources = ()


class XmlAlreadyOpenTest(unittest.TestCase):
    def test_open_existing_xml_tab_skips_resolution(self):
        tab_id = "base.resolved_xml:items.xml"
        ctx = _Ctx()
        ctx.ui = _FocusUi(tab_id)
        path = Path("configs") / "items.xml"

        class Tasks:
            def run(self, *_args, **_kwargs):
                raise AssertionError("повторный XML-резолв не должен запускаться")

        ctx.tasks = Tasks()
        opener = XmlFileOpener(ctx, _Session())

        self.assertTrue(opener.open_resolved(path, line_number=7))

        self.assertEqual(ctx.ui.focused, [tab_id])
        self.assertEqual(ctx.ui.activated, [(tab_id, 7)])
        self.assertIn("Файл уже открыт", "\n".join(ctx.ui.logs))


class XmlResolveFailureTest(unittest.TestCase):
    def setUp(self):
        self.ctx = _Ctx()
        self.opener = XmlFileOpener(self.ctx, _Session())
        self.path = Path("configs") / "items.xml"

    def test_resolve_error_publishes_failure_with_request_id(self):
        self.opener._on_resolved(
            self.path,
            None,
            RuntimeError("boom"),
            request_id=17,
        )

        self.assertEqual(
            self.ctx.ui.failed,
            [(self.path, "Ошибка XML-резолва", 17)],
        )
        self.assertIn("boom", self.ctx.ui.error)

    def test_external_editor_error_publishes_failure_with_request_id(self):
        self.opener._on_resolved_external(
            self.path,
            None,
            RuntimeError("boom"),
            request_id=18,
        )

        self.assertEqual(
            self.ctx.ui.failed,
            [(self.path, "Ошибка XML-резолва", 18)],
        )

    def test_success_passes_request_id_to_tab(self):
        self.opener._on_resolved(
            self.path,
            _Result(),
            None,
            request_id=19,
        )

        self.assertEqual(len(self.ctx.ui.opened), 1)
        _args, kwargs = self.ctx.ui.opened[0]
        self.assertEqual(kwargs["request_id"], 19)
        self.assertEqual(kwargs["line_sources"], ())


if __name__ == "__main__":
    unittest.main()
