# -*- coding: utf-8 -*-
"""Небольшой debounce-таймер для поисковых строк.

Поиск по дереву и поиск внутри файла не должны пересчитываться на каждый
символ: при быстром наборе это вызывает лишние проходы по тысячам строк и
мерцание подсветки. Все три поисковые панели используют одну задержку.
"""

from PyQt5.QtCore import QObject, QTimer

from sdk.constants import DEFAULT_SEARCH_DELAY_MS


class Debounce(QObject):
    """Однократный таймер, вызывающий callback после паузы."""

    def __init__(self, callback, interval=DEFAULT_SEARCH_DELAY_MS, parent=None):
        super().__init__(parent)
        self._callback = callback
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(int(interval))
        self._timer.timeout.connect(self._callback)

    def schedule(self):
        """Перезапустить отсчёт задержки."""
        self._timer.start()

    def cancel(self):
        self._timer.stop()

    def is_active(self):
        return self._timer.isActive()

    def flush(self):
        """Выполнить отложенный вызов немедленно (если он был запланирован)."""
        if not self._timer.isActive():
            return False
        self._timer.stop()
        self._callback()
        return True
