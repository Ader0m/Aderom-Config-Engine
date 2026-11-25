# -*- coding: utf-8 -*-
"""Use cases проекта: откат пункта предпросмотра.

Сценарий не знает про Qt: UI обновляет VFS, вкладки и статус.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

from app.patching.models import PreviewItem
from .models import ProjectError


@dataclass(frozen=True)
class RollbackResult:
    """Что откат реально удалил и какие пути нужно инвалидировать."""

    removed_paths: Tuple[Path, ...]
    removed_resolved: Tuple[str, ...]
    invalidate_paths: Tuple[Path, ...]


def rollback_preview_item(
    project_service,
    item: PreviewItem,
) -> RollbackResult:
    """Удалить черновики файлов, из которых собран пункт патча."""
    if not project_service.is_active:
        raise ProjectError("Проект не активен: откат невозможен.")

    paths = [Path(path) for path in item.rollback_paths]
    resolved_ids = [str(key) for key in item.rollback_resolved_ids]
    removed_paths = []
    removed_resolved = []
    invalidate_paths = list(paths)

    for path in paths:
        try:
            if project_service.remove_draft(path):
                removed_paths.append(path)
                continue
            if project_service.remove_asset_draft(path):
                removed_paths.append(path)
        except ProjectError as exc:
            raise ProjectError(
                "Не удалось откатить изменения:\n%s" % exc
            ) from exc

    for key in resolved_ids:
        try:
            draft = project_service.get_resolved_draft(key)
            if draft is not None and draft.root_path is not None:
                invalidate_paths.append(Path(draft.root_path))
            if project_service.remove_resolved_draft(key):
                removed_resolved.append(key)
        except ProjectError as exc:
            raise ProjectError(
                "Не удалось откатить изменения:\n%s" % exc
            ) from exc

    if not removed_paths and not removed_resolved:
        raise ProjectError(
            "Не удалось откатить изменения: черновик уже отсутствует."
        )

    return RollbackResult(
        removed_paths=tuple(removed_paths),
        removed_resolved=tuple(removed_resolved),
        invalidate_paths=tuple(invalidate_paths),
    )


__all__ = ["RollbackResult", "rollback_preview_item"]
