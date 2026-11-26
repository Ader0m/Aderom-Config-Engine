# -*- coding: utf-8 -*-
"""Feature flags релизного режима.

``PATCH_WRITE_EXPERIMENTAL`` включает пользовательскую запись патчей
после release gate Этапа 8.6. Паритет 19023/19024 остаётся
задокументированным открытым отличием, поэтому включён экспериментальный,
а не боевой режим. Откат — вернуть ``False``.
"""

PATCH_WRITE_EXPERIMENTAL = True


def patch_write_enabled() -> bool:
    """Разрешена ли запись патчей в текущей сборке."""
    return PATCH_WRITE_EXPERIMENTAL
