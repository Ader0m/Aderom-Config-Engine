# -*- coding: utf-8 -*-
"""Адаптер ``ctx.paths``: пути приложения + каноническая идентичность.

SDK не может импортировать ``core``, поэтому реализация ``key``/``same``
живёт в порту ``core.api.paths``, а модули получают готовый адаптер.
"""

from __future__ import annotations

from pathlib import Path

from core.api.paths import path_key, same_path
from sdk.contracts.paths import AppPaths


class PathsAdapter:
    """Неизменяемые пути приложения и единая идентичность путей."""

    def __init__(self, app_paths: AppPaths):
        self.app_dir = Path(app_paths.app_dir)
        self.generated_dir = Path(app_paths.generated_dir)
        self.projects_dir = Path(app_paths.projects_dir)

    def key(self, path) -> str:
        return path_key(path)

    def same(self, left, right) -> bool:
        return same_path(left, right)
