# -*- coding: utf-8 -*-
"""OverlayBuilder: неизменяемый ``ProjectOverlay`` из снимка черновиков.

Собирает overlay один раз на состояние черновиков и хранит его до
следующего изменения. Не знает про Qt, UI и предпросмотр.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from core.api.paths import path_key
from sdk.contracts import ProjectOverlay

from .models import Project


class OverlayBuilder:
    """Сборка и кэш overlay по ревизиям черновиков проекта."""

    def __init__(self, state_lock=None):
        # Тот же замок, под которым мутируются черновики проекта:
        # иначе снимок ключа и overlay может разъехаться с данными.
        self._lock = (
            state_lock if state_lock is not None else threading.RLock()
        )
        self._cache = None
        self._cache_key = None
        # Поколение overlay: даже при повторном появлении того же ключа
        # (удалённый и заново созданный черновик) token обязан измениться.
        self._generation = 0

    def clear(self) -> None:
        with self._lock:
            self._cache = None
            self._cache_key = None

    @staticmethod
    def state_key(project):
        """Ключ состояния: проект + ревизии черновиков."""
        if project is None:
            return None
        return (
            str(project.id),
            tuple(
                (
                    path_key(draft.path),
                    int(draft.revision),
                    bool(draft.is_new),
                )
                for draft in sorted(
                    project.drafts.values(),
                    key=lambda item: str(item.path).lower(),
                )
            ),
            tuple(
                (
                    path_key(draft.root_path),
                    str(draft.tab_id),
                    int(draft.revision),
                )
                for draft in sorted(
                    project.resolved_drafts.values(),
                    key=lambda item: str(item.root_path).lower(),
                )
            ),
        )

    def build(self, project: Project, is_current=None) -> ProjectOverlay:
        """Собрать overlay или вернуть кэш для того же состояния.

        ``is_current`` проверяется под замком builder'а: если проект уже
        не активен, кэш сбрасывается и возвращается пустой overlay.
        """
        with self._lock:
            if project is None:
                return ProjectOverlay()
            if is_current is not None and not is_current(project):
                self._cache = None
                self._cache_key = None
                return ProjectOverlay()

            key = self.state_key(project)
            if self._cache is not None and key == self._cache_key:
                return self._cache

            self._generation += 1

            source_files = {}
            new_files = set()
            for draft in project.drafts.values():
                path = Path(draft.path)
                source_files[path] = str(draft.current_text or "")
                if draft.is_new:
                    new_files.add(path)

            resolved_edits = {}
            for draft in project.resolved_drafts.values():
                resolved_edits[Path(draft.root_path)] = (
                    str(draft.original_text or ""),
                    str(draft.current_text or ""),
                    tuple(draft.line_sources or ()),
                )

            revision_token = (
                hashlib.sha1(
                    repr((key, self._generation)).encode(
                        "utf-8",
                        "replace",
                    )
                ).hexdigest()
                if key is not None
                else ""
            )
            overlay = ProjectOverlay(
                source_files=source_files,
                resolved_edits=resolved_edits,
                new_files=frozenset(new_files),
                revision_token=revision_token,
            )
            self._cache = overlay
            self._cache_key = key
            return overlay


__all__ = ["OverlayBuilder"]
