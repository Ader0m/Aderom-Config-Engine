# -*- coding: utf-8 -*-
"""Состояние сессии item_browser.

Лесом владеет платформа. Модуль хранит только каталог конфигов,
полученный из события пересборки VFS, и читает актуальный лес через SDK.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def resolved_tab_id(relative_path) -> str:
    """id resolved-вкладки файла предметов.

    Формат совпадает с базовым LTX-вьюером: item_browser не импортирует
    другой модуль, но проверяет уже открытый resolved-документ, если он
    есть. Без base черновик просто не находится.
    """
    return "base.resolved:%s" % relative_path


@dataclass
class ItemSession:
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
