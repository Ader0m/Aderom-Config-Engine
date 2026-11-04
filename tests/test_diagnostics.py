# -*- coding: utf-8 -*-
import time
import unittest

from tests.support import SRC  # noqa: F401

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QTextCursor

from ui.diagnostics import DiagnosticsLog
from ui.theme import PALETTE

_app = QApplication.instance() or QApplication([])


class DiagnosticsTest(unittest.TestCase):
    def _block_color(self, log, block_number=0):
        block = log.document().findBlockByNumber(block_number)
        cursor = QTextCursor(block)
        cursor.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
        return cursor.charFormat().foreground().color().name()

    def test_new_line_is_amber(self):
        log = DiagnosticsLog()
        log.append_message("hello")

        self.assertEqual(
            self._block_color(log),
            PALETTE.accent_amber.lower(),
        )

    def test_line_fades_to_base(self):
        log = DiagnosticsLog()
        log.FADE_SECONDS = 0.05
        log.append_message("hello")
        time.sleep(0.06)
        log._on_tick()

        self.assertEqual(
            self._block_color(log),
            PALETTE.text.lower(),
        )
        self.assertEqual(log._fades, [])

    def test_status_updates_same_block(self):
        log = DiagnosticsLog()
        log.append_message("start")
        log.update_message("progress", "1/10")
        log.update_message("progress", "10/10")

        self.assertEqual(log.document().blockCount(), 2)
        block = log.document().findBlockByNumber(1)
        self.assertEqual(block.text(), "10/10")


if __name__ == "__main__":
    unittest.main()
