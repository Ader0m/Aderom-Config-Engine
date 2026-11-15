# -*- coding: utf-8 -*-
"""Контроллер подсказок: popup, вставка и undo."""

import unittest

from tests.qt_support import qapp
from tests.support import SRC  # noqa: F401

from PyQt5.Qsci import QsciScintilla
from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtWidgets import QApplication, QPlainTextEdit, QWidget
from PyQt5.QtTest import QTest

from core.catalogs.colors import ColorDef, ColorSet
from sdk.hints import HintItem, HintResult, HintTargetInfo
from ui.hints import HintService
from ui.hints.providers.colors import ColorTagProvider
from modules.base_module.item_viewer import LocalizedDescriptionEdit


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


class ColorHintPopupTest(unittest.TestCase):
    """Подсказка цветов не завершает правку описания и не теряет popup."""

    def setUp(self):
        self.service = HintService(
            services=None,
            events=None,
            tasks=None,
            forest_getter=lambda: None,
            overlay_getter=lambda: None,
        )
        self.service.register(ColorTagProvider(self.service.catalogs))
        entries = tuple(
            ColorDef("ui_%02d" % index, (index, 100, 200, 255))
            for index in range(6)
        )
        self.service.catalogs.colors = ColorSet(
            by_name={entry.name: entry for entry in entries},
            entries=entries,
            signature="test",
        )

        self.window = QWidget()
        self.edit = LocalizedDescriptionEdit(self.window)
        self.window.resize(640, 320)
        self.window.show()
        self.edit.set_edit_allowed(True)
        self.edit.set_engine_text("%c[")
        self.edit.begin_editing()
        cursor = self.edit.textCursor()
        cursor.movePosition(cursor.End)
        self.edit.setTextCursor(cursor)
        self.handle = self.service.attach(
            self.edit,
            target=HintTargetInfo(
                surface="engine_text",
                language="localization_text",
            ),
        )
        self.handle._refresh()
        _app.processEvents()

    def tearDown(self):
        self.handle.detach()
        self.window.close()
        self.service.close()

    def test_navigation_and_commit_keep_editing(self):
        popup = self.handle._popup
        self.assertTrue(popup.isVisible())
        self.assertTrue(self.edit.is_editing())
        self.assertEqual(popup.focusPolicy(), Qt.NoFocus)
        self.assertEqual(popup.view.focusPolicy(), Qt.NoFocus)

        for key in (
            Qt.Key_Down,
            Qt.Key_Down,
            Qt.Key_Up,
            Qt.Key_PageDown,
            Qt.Key_PageUp,
        ):
            QTest.keyClick(self.edit, key)
            _app.processEvents()

        self.assertTrue(self.edit.is_editing())
        self.assertTrue(popup.isVisible())

        QTest.keyClick(self.edit, Qt.Key_Return)
        _app.processEvents()

        text = self.edit.toPlainText()
        self.assertTrue(
            text in ("%c[default]", "%c[ui_00]"),
            text,
        )
        self.assertTrue(text.endswith("]"), text)
        self.assertTrue(self.edit.is_editing())

    def test_event_filter_swallows_adapter_errors(self):
        original = self.handle._adapter

        class _BrokenAdapter:
            def widget(self):
                raise RuntimeError("adapter is gone")

        self.handle._adapter = _BrokenAdapter()
        try:
            with self.assertLogs("ui.hints.controller", level="ERROR"):
                accepted = self.handle.eventFilter(
                    None,
                    QEvent(QEvent.KeyPress),
                )
        finally:
            self.handle._adapter = original

        self.assertFalse(accepted)

    def test_commit_row_slot_swallows_errors(self):
        original = self.handle._commit_current

        def broken():
            raise RuntimeError("commit failed")

        self.handle._commit_current = broken
        try:
            with self.assertLogs("ui.hints.controller", level="ERROR"):
                self.handle._commit_row(0)
        finally:
            self.handle._commit_current = original

        self.assertIsNone(self.handle._result)


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
