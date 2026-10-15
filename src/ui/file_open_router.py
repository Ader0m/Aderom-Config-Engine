# -*- coding: utf-8 -*-
"""Маршрутизатор открытия файлов."""

import logging
from pathlib import Path

from sdk.events import Events

log = logging.getLogger(__name__)


class FileOpenHandler:
    priority = 100

    def can_open(self, path):
        return False

    def open(self, path, api):
        raise NotImplementedError


class TextEditorHandler(FileOpenHandler):
    """Обработчик по умолчанию: текстовый редактор."""

    priority = 0

    def __init__(self, editor):
        self.editor = editor

    def open(self, path, api):
        self.editor.open_file(str(path))
        return True


class FileOpenRouter:
    def __init__(self, events=None):
        self._handlers = []
        self.default = None
        self.events = events

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
                    self._notify(p)
                    return True
            except Exception:
                log.exception("Обработчик открытия файла упал: %r", h)

        if self.default is not None:
            self.default.open(p, api)
            self._notify(p)
            return True

        return False

    def _notify(self, p):
        if self.events is not None:
            self.events.publish(Events.FILE_OPENED, p)