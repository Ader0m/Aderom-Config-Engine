# -*- coding: utf-8 -*-
"""Feature flags переходного периода.

Флаг ``PATCH_WRITE_EXPERIMENTAL`` замораживает запись патчей до Этапа 5.
До этого разрешён только предпросмотр. Включение флага допустимо после
Этапов 5 и 8, вместе с авторизацией записи и release gate.
"""

PATCH_WRITE_EXPERIMENTAL = False


def patch_write_enabled() -> bool:
    """Разрешена ли запись патчей в текущей сборке."""
    return PATCH_WRITE_EXPERIMENTAL
