# -*- coding: utf-8 -*-
"""Каноническая идентичность путей.

Единственная реализация ``normcase(abspath(normpath(...)))`` для всего
приложения. Модули не импортируют этот модуль напрямую: платформа
передаёт им ``ctx.paths.key()``/``ctx.paths.same()``.
"""

from __future__ import annotations

import os
from pathlib import Path


def path_key(path) -> str:
    """Абсолютный нормализованный ключ без учёта регистра (Windows)."""
    return os.path.normcase(
        os.path.normpath(os.path.abspath(os.fspath(path)))
    )


def same_path(left, right) -> bool:
    """Один и тот же путь с точностью до регистра и нормализации."""
    try:
        return path_key(left) == path_key(right)
    except (TypeError, ValueError, OSError):
        return False


def as_path(path) -> Path:
    """Привести значение к ``Path``, если это возможно."""
    return path if isinstance(path, Path) else Path(path)
