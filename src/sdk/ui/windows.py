# -*- coding: utf-8 -*-
"""Описания окон и вкладок для модулей.

Модуль создаёт ``QWidget`` и передаёт платформе описание. Платформа
владеет контейнером, владельцем, раскладкой и политикой закрытия.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

WINDOW_KINDS = frozenset(
    ("document_tab", "tool_tab", "dock", "floating", "dialog")
)
DOCK_AREAS = frozenset(("left", "right", "bottom", "center"))
CLOSE_POLICIES = frozenset(("owner", "ask_if_dirty"))


@dataclass(frozen=True)
class WindowSpec:
    """Описание окна/вкладки, которой владеет модуль."""

    key: str
    title: str = ""
    kind: str = "floating"
    owner: str = ""
    area: str = ""
    size: Optional[Tuple[int, int]] = None
    min_size: Optional[Tuple[int, int]] = None
    closable: bool = True
    state_key: str = ""

    def __post_init__(self):
        if not str(self.key or "").strip():
            raise ValueError("WindowSpec.key не может быть пустым")
        if self.kind not in WINDOW_KINDS:
            raise ValueError("Неизвестный WindowSpec.kind: %s" % self.kind)
        if self.area and self.area not in DOCK_AREAS:
            raise ValueError("Неизвестный WindowSpec.area: %s" % self.area)


@dataclass(frozen=True)
class DockSpec:
    """Описание док-панели модуля."""

    key: str
    title: str = ""
    area: str = "right"
    owner: str = ""
    size: Optional[Tuple[int, int]] = None
    closable: bool = True
    visible: bool = True
    state_key: str = ""

    def __post_init__(self):
        if not str(self.key or "").strip():
            raise ValueError("DockSpec.key не может быть пустым")
        if self.area not in DOCK_AREAS:
            raise ValueError("Неизвестный DockSpec.area: %s" % self.area)


@dataclass(frozen=True)
class TabSpec:
    """Описание вкладки, которой владеет модуль."""

    id: str
    title: str = ""
    owner: str = ""
    kind: str = "tool_tab"
    icon: object = None
    dirty: bool = False
    busy: bool = False
    close_policy: str = "owner"
    source_path: Optional[Path] = None

    def __post_init__(self):
        if not str(self.id or "").strip():
            raise ValueError("TabSpec.id не может быть пустым")
        if self.close_policy not in CLOSE_POLICIES:
            raise ValueError(
                "Неизвестный TabSpec.close_policy: %s"
                % self.close_policy
            )
