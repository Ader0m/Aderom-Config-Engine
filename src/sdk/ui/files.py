# -*- coding: utf-8 -*-
"""Обработчики открытия и чтения файлов."""

from __future__ import annotations

from sdk.features.documents.models import FileView


class FileOpenHandler:
    """
    Контракт обработчика открытия файла.
    Регистрируется через ctx.ui.register_open_handler(...).
    Чем выше priority, тем раньше обработчик претендует на файл.

    ``contexts`` — контексты открытия, в которых обработчик участвует:
    ``view``, ``edit``, ``preview`` или ``patch``. По умолчанию только
    просмотр. Обработчик, который умеет открывать файл в нескольких
    контекстах, перечисляет их явно.

    ``handler_id`` — стабильный идентификатор обработчика в пределах
    владельца. Он обязателен, если один владелец регистрирует несколько
    обработчиков одного класса: идентификатор не зависит от порядка
    регистрации. В ``handler_id`` нельзя использовать ``|`` и ``#``.
    """

    CONTEXTS = ("view", "edit", "preview", "patch")
    contexts = ("view",)
    handler_id = ""

    priority = 100

    def supports_context(self, context):
        """Может ли обработчик открыть файл в указанном контексте."""
        contexts = self.contexts
        if isinstance(contexts, str):
            contexts = (contexts,)
        try:
            values = tuple(str(item).lower() for item in contexts)
        except TypeError:
            return False
        return str(context or "view").lower() in values

    def can_open(self, path):
        return False

    def open(self, path, api):
        raise NotImplementedError


class FileReader:
    """Читатель файла для проекта.

    Регистрируется через ``ctx.projects.register_file_reader(...)``. Проект
    выбирает первый подходящий читатель по ``priority`` (больше — раньше)
    и ``can_read``. Читатель возвращает
    :class:`sdk.features.documents.FileView` — итоговый вид файла для
    предпросмотра, поиска или других сценариев.

    ``read`` вызывается в фоновом потоке, поэтому не должен трогать
    Qt-виджеты и изменяемое состояние проекта.
    """

    priority = 100

    def can_read(self, path, data=None):
        return False

    def read(self, path, data=None):
        raise NotImplementedError
