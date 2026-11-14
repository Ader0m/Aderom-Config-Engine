# -*- coding: utf-8 -*-
"""Реестр читателей файлов, которые поставляют модули.

Читатель превращает файл (путь + байты) в :class:`sdk.api.FileView`.
Проект использует его, когда ему нужен «итоговый вид» файла: сейчас —
в предпросмотре патча, дальше — в поиске, экспорте и других сценариях.

Это не событие: чтение — чистая трансформация данных, и проект должен
получить ровно одно представление от первого подходящего читателя.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import List, Optional

from sdk.api import FileView
from sdk.contracts import FileReader


class FileReaderError(Exception):
    """Читатель не смог построить представление файла."""


class FileReaderSubscription:
    """Подписка на реестр: позволяет модулю снять читатель."""

    def __init__(self, registry, reader):
        self._registry = registry
        self._reader = reader
        self._active = True

    def unsubscribe(self):
        if not self._active:
            return
        self._active = False
        self._registry.unregister(self._reader)


class ProjectFileReaderRegistry:
    """Упорядоченный по priority набор читателей."""

    def __init__(self):
        self._readers: List[FileReader] = []
        self._lock = threading.RLock()

    def register(self, reader) -> FileReaderSubscription:
        if not isinstance(reader, FileReader):
            raise TypeError(
                "Читатель должен наследовать sdk.FileReader: %r" % reader
            )
        with self._lock:
            self._readers.append(reader)
        return FileReaderSubscription(self, reader)

    def unregister(self, reader) -> bool:
        with self._lock:
            try:
                self._readers.remove(reader)
            except ValueError:
                return False
        return True

    def readers(self):
        with self._lock:
            return tuple(
                sorted(
                    self._readers,
                    key=lambda item: -int(getattr(item, "priority", 0) or 0),
                )
            )

    def read(
        self,
        path,
        data: Optional[bytes] = None,
    ) -> Optional[FileView]:
        path = Path(path)
        for reader in self.readers():
            name = type(reader).__name__
            try:
                matched = reader.can_read(path, data)
            except Exception as exc:
                raise FileReaderError(
                    "%s.can_read(%s): %s" % (name, path.name, exc)
                ) from exc

            if not matched:
                continue

            try:
                view = reader.read(path, data)
            except Exception as exc:
                raise FileReaderError(
                    "%s.read(%s): %s" % (name, path.name, exc)
                ) from exc

            if isinstance(view, FileView):
                return view
            if isinstance(view, str):
                return FileView(text=view, label=name)
            if view is None:
                continue
            raise FileReaderError(
                "%s.read(%s) вернул %r вместо FileView"
                % (name, path.name, type(view).__name__)
            )
        return None
