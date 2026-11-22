# -*- coding: utf-8 -*-
"""Сессия проекта: единственный писатель активного состояния.

``ProjectSession`` владеет активным проектом, режимами и настройками
модулей. Мутации выполняются явными командами под одним замком; чтение
скалярного состояния идёт по неизменяемому снимку ``ProjectState``.

Тексты черновиков здесь не копируются: ими владеет ``DraftStore``, а
снимок для фонового предпросмотра появится вместе с use case
предпросмотра (Этап 3). Модуль не знает про Qt и UI.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping, Optional, Tuple, TypeVar

from .models import Project, ProjectError

T = TypeVar("T")


@dataclass(frozen=True)
class ProjectState:
    """Неизменяемый снимок скалярного состояния сессии.

    Не содержит текстов черновиков и ссылок на изменяемые словари:
    ``module_settings`` — только для чтения, ``modules`` — кортеж.
    """

    id: str
    name: str
    root_dir: Path
    modules: Tuple[str, ...]
    save_mode: str
    xml_patch_mode: str
    module_settings: Mapping[str, object]
    created_at: str
    revision: int


class ProjectSession:
    """Активный проект и сериализованные команды над ним."""

    def __init__(self):
        self._lock = threading.RLock()
        self._active: Optional[Project] = None
        self._revision = 0

    # -------------------------------------------------- состояние
    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._active is not None

    @property
    def active(self) -> Optional[Project]:
        with self._lock:
            return self._active

    @property
    def revision(self) -> int:
        """Монотонная ревизия состояния сессии."""
        with self._lock:
            return self._revision

    def require_active(self) -> Project:
        with self._lock:
            return self._require_locked()

    def state(self) -> Optional[ProjectState]:
        """Неизменяемый снимок скалярного состояния."""
        with self._lock:
            project = self._active
            if project is None:
                return None
            return ProjectState(
                id=str(project.id),
                name=str(project.name),
                root_dir=Path(project.root_dir),
                modules=tuple(project.modules),
                save_mode=str(project.save_mode),
                xml_patch_mode=str(project.xml_patch_mode),
                module_settings=MappingProxyType(
                    dict(project.module_settings)
                ),
                created_at=str(project.created_at),
                revision=self._revision,
            )

    # -------------------------------------------------- жизненный цикл
    def activate(self, project: Project) -> Project:
        with self._lock:
            self._active = project
            self._revision += 1
            return project

    def deactivate(self) -> None:
        with self._lock:
            if self._active is None:
                return
            self._active = None
            self._revision += 1

    # -------------------------------------------------- команды
    def read(self, command: Callable[[Project], T]) -> T:
        """Прочитать что-то у активного проекта под замком сессии."""
        with self._lock:
            return command(self._require_locked())

    def write(self, command: Callable[[Project], T]) -> T:
        """Выполнить мутацию активного проекта одним писателем.

        Это сериализованный mutex, а не отложенная очередь: команда
        выполняется в вызывающем потоке. Ревизия растёт только после
        успешной команды. Команда сама отвечает за атомарность: при
        ошибке записи она обязана восстановить изменённые поля.
        """
        with self._lock:
            result = command(self._require_locked())
            self._revision += 1
            return result

    # -------------------------------------------------- внутреннее
    def _require_locked(self) -> Project:
        if self._active is None:
            raise ProjectError("Проект не создан")
        return self._active


__all__ = ["ProjectSession", "ProjectState"]
