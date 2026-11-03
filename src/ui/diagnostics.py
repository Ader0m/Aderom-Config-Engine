# -*- coding: utf-8 -*-
"""Окно диагностики с плавным затуханием новых строк."""

import time

from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QColor, QTextCharFormat, QTextCursor
from PyQt5.QtWidgets import QTextEdit

try:  # запуск с корня проекта (src в sys.path)
    from ui.theme import PALETTE
except ImportError:  # запуск как пакета
    from .theme import PALETTE


class DiagnosticsLog(QTextEdit):
    """QTextEdit, где каждая новая строка гаснет из accent_amber в text."""

    FADE_SECONDS = 5.0
    TICK_MS = 100

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)

        self._amber = QColor(PALETTE.accent_amber)
        self._base = QColor(PALETTE.text)
        self._fades = []  # [{"block": int, "start": monotonic}]
        self._status_blocks = {}  # key -> block number

        self._timer = QTimer(self)
        self._timer.setInterval(self.TICK_MS)
        self._timer.timeout.connect(self._on_tick)

    # -------------------------------------------------- добавление
    def append_message(self, message):
        lines = self._split_lines(message)
        self._insert_lines(lines, self._amber)
        self.ensureCursorVisible()

        if self._fades and not self._timer.isActive():
            self._timer.start()

    def update_message(self, key, message):
        """Обновить ранее добавленную строку по ключу.

        Для прогресса/статуса: не создаёт новую строку на каждое сообщение.
        Текст статуса предполагается однострочным (переводы строк
        схлопываются в пробелы).
        """
        text = str(message).replace("\r\n", "\n").replace("\r", "\n")
        text = text.replace("\n", " ")

        block_number = self._status_blocks.get(key)
        block = (
            self.document().findBlockByNumber(block_number)
            if block_number is not None
            else None
        )

        if block is None or not block.isValid():
            self._status_blocks.pop(key, None)
            blocks = self._insert_lines([text], self._amber)
            if blocks:
                self._status_blocks[key] = blocks[-1]
                self.ensureCursorVisible()
        else:
            cursor = QTextCursor(block)
            cursor.movePosition(QTextCursor.StartOfBlock)
            cursor.movePosition(
                QTextCursor.EndOfBlock,
                QTextCursor.KeepAnchor,
            )
            fmt = QTextCharFormat()
            fmt.setForeground(self._amber)
            cursor.insertText(text, fmt)

            self._fades = [
                item for item in self._fades
                if item["block"] != block_number
            ]
            self._fades.append({
                "block": block_number,
                "start": time.monotonic(),
            })

        if self._fades and not self._timer.isActive():
            self._timer.start()

    def append(self, message):
        """Совместимость с QTextEdit.append."""
        self.append_message(message)

    # -------------------------------------------------- вставка строк
    @staticmethod
    def _split_lines(message):
        text = str(message).replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")
        return lines or [""]

    def _insert_lines(self, lines, color):
        now = time.monotonic()
        blocks = []

        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.End)

        for line in lines:
            if not self.document().isEmpty():
                cursor.insertBlock()

            fmt = QTextCharFormat()
            fmt.setForeground(color)
            cursor.insertText(line, fmt)

            block = cursor.block()
            blocks.append(block.blockNumber())
            self._fades.append({
                "block": block.blockNumber(),
                "start": now,
            })

        self.setTextCursor(cursor)
        return blocks

    # -------------------------------------------------- затухание
    def _on_tick(self):
        now = time.monotonic()
        remaining = []

        for item in self._fades:
            elapsed = now - item["start"]
            if elapsed >= self.FADE_SECONDS:
                self._apply_color(item["block"], self._base)
                continue

            ratio = max(0.0, min(1.0, elapsed / self.FADE_SECONDS))
            self._apply_color(
                item["block"],
                self._blend(self._amber, self._base, ratio),
            )
            remaining.append(item)

        self._fades = remaining
        if not self._fades:
            self._timer.stop()

    def _apply_color(self, block_number, color):
        block = self.document().findBlockByNumber(block_number)
        if not block.isValid():
            return

        cursor = QTextCursor(self.document())
        cursor.setPosition(block.position())
        cursor.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)

        fmt = QTextCharFormat()
        fmt.setForeground(color)
        cursor.mergeCharFormat(fmt)

    @staticmethod
    def _blend(start, end, ratio):
        return QColor(
            int(start.red() + (end.red() - start.red()) * ratio),
            int(start.green() + (end.green() - start.green()) * ratio),
            int(start.blue() + (end.blue() - start.blue()) * ratio),
        )
