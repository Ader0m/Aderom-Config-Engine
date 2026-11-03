# -*- coding: utf-8 -*-
"""Индикатор фоновой предсборки в правом нижнем углу."""

from PyQt5.QtCore import QRect, Qt
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import QWidget

try:  # запуск с корня проекта (src в sys.path)
    from ui.theme import PALETTE
except ImportError:  # запуск как пакета
    from .theme import PALETTE


class PrebuildPanel(QWidget):
    """Небольшая панель: «Сборка x/n» с зелёным заполнением."""

    GREEN = QColor("#2E7D32")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = "idle"
        self._done = 0
        self._total = 0
        self._error = None

        self.setFixedSize(230, 20)
        self.setVisible(False)

    def set_status(self, state, done, total, error=None):
        self._state = state or "idle"
        self._done = int(done or 0)
        self._total = int(total or 0)
        self._error = error

        self.setVisible(self._state in ("running", "done"))
        if self._error:
            self.setToolTip("Фоновая сборка: %s" % self._error)
        else:
            self.setToolTip("Подготовка итоговых файлов резолвера")

        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        rect = self.rect()
        painter.fillRect(rect, QColor(PALETTE.bg_titlebar))

        if self._state == "running" and self._total > 0:
            ratio = max(0.0, min(1.0, self._done / self._total))
            fill = QRect(
                rect.left(),
                rect.top(),
                int(rect.width() * ratio),
                rect.height(),
            )
            painter.fillRect(fill, self.GREEN)

        if self._state == "running":
            if self._total:
                text = "Сборка %d/%d" % (self._done, self._total)
            else:
                text = "Сборка…"
            color = PALETTE.text_bright
        elif self._state == "done":
            text = "Сборка завершена"
            color = PALETTE.text
        else:
            text = ""
            color = PALETTE.text

        painter.setPen(QColor(color))
        painter.drawText(rect, Qt.AlignCenter, text)

        painter.setPen(QColor(PALETTE.border))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
