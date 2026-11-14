# -*- coding: utf-8 -*-
"""Состояние сессии базового модуля.

Лесом владеет платформа (WorkspaceService). Сессия хранит только
локальный fallback до публикации TREE_READY и читает актуальный лес
через SDK.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ModuleSession:
    """Текущий каталог конфигов и доступ к workspace-лесу."""

    ctx: object = None
    config_dir: Optional[Path] = None
    _forest: object = None

    @property
    def forest(self):
        if self.ctx is not None:
            getter = getattr(self.ctx.ui, "workspace_forest", None)
            if callable(getter):
                value = getter()
                if value is not None:
                    return value
        return self._forest

    @forest.setter
    def forest(self, value):
        self._forest = value

    @property
    def has_tree(self) -> bool:
        return self.forest is not None
