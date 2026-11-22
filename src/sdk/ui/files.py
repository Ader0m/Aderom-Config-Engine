# -*- coding: utf-8 -*-
"""Обработчики открытия и чтения файлов."""

from __future__ import annotations

from sdk.features.documents.models import FileView


class FileOpenHandler:
    """
    Контракт обработчика открытия файла.
    Регистрируется через ctx.ui.register_open_handler(...).
    Чем выше priority, тем раньше обработчик претендует на файл.
    """

    priority = 100

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
