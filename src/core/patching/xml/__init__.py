# -*- coding: utf-8 -*-
"""Патч-конвейер XML: документ, diff, план, генерация, проверка.

Публичный вход — :class:`XmlPatchService`. Импорт ленивый, чтобы подмодули
пакета можно было использовать без циклической загрузки сервиса.
"""

__all__ = ["XmlPatchService"]


def __getattr__(name):
    if name == "XmlPatchService":
        from core.patching.xml.service import XmlPatchService

        return XmlPatchService
    raise AttributeError(name)
