# -*- coding: utf-8 -*-
"""Состояние сессии базового модуля.

Компоненты модуля делят одно состояние, но не знают друг о друге.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ModuleSession:
    """Текущий каталог конфигов и построенное дерево."""

    config_dir: Optional[Path] = None
    forest: object = None

    @property
    def has_tree(self) -> bool:
        return self.forest is not None
