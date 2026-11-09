# -*- coding: utf-8 -*-
"""Патч-конвейер LTX: документ, diff, план, генерация, проверка.

Публичный вход — :class:`LtxPatchService`. Импорт ленивый, чтобы подмодули
пакета можно было использовать без циклической загрузки сервиса.
"""

__all__ = ["LtxPatchService"]


def __getattr__(name):
    if name == "LtxPatchService":
        from core.patching.ltx.service import LtxPatchService

        return LtxPatchService
    raise AttributeError(name)
