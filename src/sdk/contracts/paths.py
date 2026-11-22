# -*- coding: utf-8 -*-
"""Пути приложения в контракте модулей.

Реализация идентичности путей намеренно не живёт в SDK: модули получают
``ctx.paths`` с методами ``key`` и ``same`` от платформы. Поэтому здесь
остаются только неизменяемый DTO путей и протокол этого API.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class AppPaths:
    """Неизменяемые пути приложения, доступные модулю как ``ctx.paths``."""

    app_dir: Path
    generated_dir: Path
    projects_dir: Path


class PathsApi(Protocol):
    """Контракт ``ctx.paths``: пути приложения и идентичность путей."""

    app_dir: Path
    generated_dir: Path
    projects_dir: Path

    def key(self, path) -> str:
        """Канонический ключ пути."""

    def same(self, left, right) -> bool:
        """Один и тот же путь с точностью до регистра/нормализации."""


def as_path(path):
    """Привести значение к ``Path``, если это возможно."""
    return path if isinstance(path, Path) else Path(path)
