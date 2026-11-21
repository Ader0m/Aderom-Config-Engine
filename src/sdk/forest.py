# -*- coding: utf-8 -*-
"""Непрозрачная read-only ссылка на построенный лес.

Модуль получает :class:`ForestRef` вместо ``LtxForest``: он может читать
безопасные проекции (пути, корни, счётчики) и передавать ссылку обратно
в сервисы, но не может менять внутренние списки/словари леса.
"""

from dataclasses import FrozenInstanceError
from pathlib import Path


class ForestRef:
    """Read-only handle на лес. Сырой объект доступен только адаптерам."""

    __slots__ = ("_raw",)

    def __init__(self, forest):
        if isinstance(forest, ForestRef):
            forest = forest._raw
        object.__setattr__(self, "_raw", forest)

    def __setattr__(self, name, value):
        raise FrozenInstanceError("ForestRef is read-only")

    def __delattr__(self, name):
        raise FrozenInstanceError("ForestRef is read-only")

    @property
    def cache_id(self) -> str:
        return str(getattr(self._raw, "cache_id", "") or "")

    @property
    def scan_dir(self):
        value = getattr(self._raw, "scan_dir", None)
        return Path(value) if value else None

    @property
    def system_root(self):
        value = getattr(self._raw, "system_root", None)
        return Path(value) if value else None

    @property
    def total_files(self) -> int:
        return int(getattr(self._raw, "total_files", 0) or 0)

    @property
    def total_xml_files(self) -> int:
        return int(getattr(self._raw, "total_xml_files", 0) or 0)

    @property
    def files(self) -> tuple:
        return tuple(getattr(self._raw, "files", ()) or ())

    @property
    def xml_files(self) -> tuple:
        return tuple(getattr(self._raw, "xml_files", ()) or ())

    @property
    def roots(self) -> tuple:
        return tuple(getattr(self._raw, "roots", ()) or ())

    def __repr__(self):
        return "ForestRef(cache_id=%r)" % self.cache_id


def forest_ref(value):
    """Обернуть лес в :class:`ForestRef` (идемпотентно)."""
    if value is None or isinstance(value, ForestRef):
        return value
    return ForestRef(value)


def unwrap_forest(value):
    """Вернуть сырой лес для внутренних сервисов ядра/платформы."""
    if isinstance(value, ForestRef):
        return value._raw
    return value
