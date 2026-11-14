# -*- coding: utf-8 -*-
"""Единая идентичность путей для SDK и модулей.

Модули не должны повторять ``normcase(abspath(...))`` в каждом файле:
разные варианты normalize дают разные ключи для одного и того же файла.
"""

import os
from pathlib import Path


def path_key(path) -> str:
    """Абсолютный нормализованный ключ без учёта регистра (Windows)."""
    return os.path.normcase(
        os.path.normpath(os.path.abspath(os.fspath(path)))
    )


def same_path(left, right) -> bool:
    """Один и тот же путь с точностью до регистра/нормализации."""
    try:
        return path_key(left) == path_key(right)
    except (TypeError, ValueError, OSError):
        return False


def as_path(path):
    """Привести значение к ``Path``, если это возможно."""
    return path if isinstance(path, Path) else Path(path)
