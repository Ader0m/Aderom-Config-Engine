# -*- coding: utf-8 -*-
"""Единая точка создания QApplication для тестов."""

import os

from tests.support import SRC  # noqa: F401

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication  # noqa: E402


def qapp():
    """Возвращает единственный QApplication на процесс."""
    return QApplication.instance() or QApplication([])
