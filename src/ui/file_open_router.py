# -*- coding: utf-8 -*-
"""Маршрутизатор открытия файлов."""

import logging
from pathlib import Path

from sdk.contracts import FileOpenHandler

log = logging.getLogger(__name__)


class TextEditorHandler(FileOpenHandler):
    """Обработчик по умолчанию: текстовый редактор (сырой файл)."""

    priority = 0

    def __init__(self, editor):
        self.editor = editor

    def open(self, path, api):
        api.log("Открытие файла: %s" % Path(path).name)
        self.editor.open_file(str(path))
        return True


class FileOpenRouter:
    """Выбирает обработчик открытия файла.

    Маршрутизатор только принимает запрос. Факт открытия публикует UI-слой
    (``EditorTabs``) после того, как виджет размещён и стал текущим.
    """

    def __init__(self):
        self._handlers = []
        self.default = None

    def register(self, handler):
        self._handlers.append(handler)

    def set_default(self, handler):
        self.default = handler

    def open(self, path, api):
        p = Path(path)

        for h in sorted(self._handlers, key=lambda x: -x.priority):
            try:
                if h.can_open(p):
                    h.open(p, api)
                    return True
            except Exception:
                log.exception("Обработчик открытия файла упал: %r", h)

        return self.open_default(p, api)

    def open_default(self, path, api):
        """Открыть в обработчике по умолчанию, минуя зарегистрированные."""
        p = Path(path)
        if self.default is None:
            return False
        self.default.open(p, api)
        return True