# -*- coding: utf-8 -*-
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

from core.models import LineSource
from ui.editor import SourceListModel

_app = QApplication.instance() or QApplication([])


class LineSourceUiTest(unittest.TestCase):
    def test_model_supports_domain_line_source(self):
        model = SourceListModel()
        model.set_sources([
            LineSource(kind="original"),
            LineSource(
                kind="file",
                display="a.ltx",
                path=Path("a.ltx"),
                open_raw=False,
            ),
            None,
        ])

        self.assertEqual(
            model.data(model.index(0, 0), Qt.DisplayRole),
            SourceListModel.ORIGINAL_LABEL,
        )
        self.assertEqual(
            model.data(model.index(1, 0), Qt.DisplayRole),
            "a.ltx",
        )
        self.assertFalse(
            model.flags(model.index(0, 0)) & Qt.ItemIsEnabled
        )
        self.assertTrue(
            model.flags(model.index(1, 0)) & Qt.ItemIsEnabled
        )


if __name__ == "__main__":
    unittest.main()
