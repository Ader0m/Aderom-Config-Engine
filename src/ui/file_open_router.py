# -*- coding: utf-8 -*-
"""Маршрутизатор открытия файлов."""

import logging
from pathlib import Path

from sdk.contracts import FileOpenHandler
from sdk.registrations import RegistrationHandle, remove_identity

log = logging.getLogger(__name__)


class TextEditorHandler(FileOpenHandler):
    """Обработчик по умолчанию: текстовый редактор (сырой файл)."""

    priority = 0

    def __init__(self, editor):
        self.editor = editor

    def open(self, path, api):
        api.log("Открытие файла: %s" % Path(path).name)
        text = None
        reader = getattr(api, "document_text", None)
        if callable(reader):
            text = reader(path)

        can_edit = getattr(api, "can_edit_documents", None)
        if callable(can_edit) and not can_edit():
            setter = getattr(api, "set_status", None)
            if callable(setter):
                setter(
                    "Проект не создан: файл открыт только для чтения",
                    5000,
                )

        self.editor.open_file(str(path), text=text)
        return True


class _HandlerEntry:
    """Обработчик открытия вместе с владельцем-модулем."""

    __slots__ = ("handler", "context", "owner")

    def __init__(self, handler, context, owner):
        self.handler = handler
        self.context = context
        self.owner = str(owner or "")


class FileOpenRouter:
    """Выбирает обработчик открытия файла.

    Маршрутизатор только принимает запрос. Факт открытия публикует UI-слой
    (``EditorTabs``) после того, как виджет размещён и стал текущим.
    """

    def __init__(self):
        self._handlers = []  # [_HandlerEntry]
        self.default = None

    def register(self, handler, context=None, owner=None):
        """Зарегистрировать обработчик и вернуть handle для снятия."""
        entry = _HandlerEntry(handler, context, owner)
        self._handlers.append(entry)
        return RegistrationHandle(
            lambda: self._remove(entry),
            entry.owner,
        )

    def unregister(self, handler, owner=None):
        """Снять обработчик. ``owner=None`` — внутренняя очистка платформы."""
        wanted = None if owner is None else str(owner)
        for entry in tuple(self._handlers):
            if entry.handler is not handler:
                continue
            if wanted is not None and entry.owner != wanted:
                continue
            return self._remove(entry)
        return False

    def _remove(self, entry):
        return remove_identity(self._handlers, entry)

    def set_default(self, handler):
        self.default = handler

    def open(self, path, api):
        p = Path(path)

        for entry in sorted(
            tuple(self._handlers),
            key=lambda item: -item.handler.priority,
        ):
            handler = entry.handler
            context = entry.context
            try:
                if not handler.can_open(p):
                    continue
                # Модульный обработчик получает свой ModuleContext, а не
                # сырой PlatformApi. Платформенный default — PlatformApi.
                result = handler.open(
                    p,
                    context if context is not None else api,
                )
                # False означает «обработчик выбран, но открытие не
                # удалось». Не подменяем ошибку открытием сырого файла.
                return bool(result)
            except Exception:
                log.exception("Обработчик открытия файла упал: %r", handler)
                # Обработчик уже выбран по типу файла; не подменяем его
                # ошибку открытием сырого файла по умолчанию.
                return False

        return self.open_default(p, api)

    def open_default(self, path, api):
        """Открыть в обработчике по умолчанию, минуя зарегистрированные."""
        p = Path(path)
        if self.default is None:
            return False
        try:
            return bool(self.default.open(p, api))
        except Exception:
            log.exception("Обработчик открытия по умолчанию упал")
            return False