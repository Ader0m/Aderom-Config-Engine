# -*- coding: utf-8 -*-
"""Контроллер подсказок: popup, вставка и undo."""

import unittest

from tests.qt_support import qapp
from tests.support import SRC  # noqa: F401

from PyQt5.Qsci import QsciScintilla
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QPlainTextEdit

from sdk.hints import HintItem, HintResult, HintTargetInfo
from ui.hints import HintService


class _Provider:
    id = "test.hints"
    priority = 100
    surfaces = frozenset({"plain"})
    languages = frozenset({"plain"})
    allow_in_comment = False

    def matches(self, context):
        if context.prefix != "xx":
            return None
        return HintResult(
            provider_id=self.id,
            items=(HintItem(label="xx_value", insert_text="xx_value"),),
            handled=True,
            replace_before=2,
            prefix="xx",
            total=1,
        )


_app = qapp()


class HintControllerTest(unittest.TestCase):
    def setUp(self):
        self.service = HintService(
            services=None,
            events=None,
            tasks=None,
            forest_getter=lambda: None,
            overlay_getter=lambda: None,
        )
        self.service.register(_Provider())

    def tearDown(self):
        self.service.close()

    def test_popup_commit_and_undo(self):
        widget = QPlainTextEdit()
        widget.show()
        widget.setPlainText("xx")
        cursor = widget.textCursor()
        cursor.setPosition(2)
        widget.setTextCursor(cursor)
        handle = self.service.attach(
            widget,
            target=HintTargetInfo(surface="plain", language="plain"),
        )
        handle._refresh()
        self.assertTrue(handle._popup.isVisible())
        self.assertEqual(handle._popup.focusPolicy(), Qt.NoFocus)

        handle._commit_current()
        self.assertEqual(widget.toPlainText(), "xx_value")
        widget.undo()
        self.assertEqual(widget.toPlainText(), "xx")

    def test_read_only_hides_popup(self):
        widget = QPlainTextEdit()
        widget.setPlainText("xx")
        widget.setReadOnly(True)
        handle = self.service.attach(
            widget,
            target=HintTargetInfo(surface="plain", language="plain"),
        )
        handle._refresh()
        self.assertFalse(handle._popup and handle._popup.isVisible())

    def test_escape_dismisses_same_context(self):
        widget = QPlainTextEdit()
        widget.show()
        widget.setPlainText("xx")
        cursor = widget.textCursor()
        cursor.setPosition(2)
        widget.setTextCursor(cursor)
        handle = self.service.attach(
            widget,
            target=HintTargetInfo(surface="plain", language="plain"),
        )
        handle._refresh()
        self.assertTrue(handle._popup.isVisible())
        handle._dismissed = handle._signature
        handle.close()
        handle._refresh()
        self.assertFalse(handle._popup.isVisible())


class ScintillaControllerTest(unittest.TestCase):
    def setUp(self):
        self.service = HintService(
            services=None,
            events=None,
            tasks=None,
            forest_getter=lambda: None,
            overlay_getter=lambda: None,
        )
        provider = _Provider()
        provider.surfaces = frozenset({"raw"})
        provider.languages = frozenset({"ltx"})
        self.service.register(provider)

    def tearDown(self):
        self.service.close()

    def test_scintilla_commit(self):
        editor = QsciScintilla()
        editor.show()
        editor.setText("xx")
        editor.setCursorPosition(0, 2)
        handle = self.service.attach(
            editor,
            target=HintTargetInfo(surface="raw", language="ltx"),
        )
        handle._refresh()
        self.assertTrue(handle._popup.isVisible())
        handle._commit_current()
        self.assertEqual(editor.text(), "xx_value")
        editor.undo()
        self.assertEqual(editor.text(), "xx")


if __name__ == "__main__":
    unittest.main()
