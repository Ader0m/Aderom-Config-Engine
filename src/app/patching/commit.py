# -*- coding: utf-8 -*-
"""Запись предпросмотра в патч-мод (исторический путь до Этапа 5).

Сценарий не знает про Qt: UI выбирает каталог и показывает сообщения.
Автоподтверждение планов и ручная сборка каталога остаются до Этапа 5
(CR-1/CR-2), поведение не меняется.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Tuple

from app.projects.models import (
    ProjectError,
    ProjectPatchPreview,
)


@dataclass(frozen=True)
class PreviewWriteResult:
    """Итог записи патч-мода."""

    mod_dir: Path
    written: Tuple[Path, ...]
    skipped: Tuple[str, ...]
    excluded_count: int


def _patch_mod_name(project_name) -> str:
    safe_name = re.sub(
        r'[<>:"/\\|?*]+',
        "_",
        str(project_name or ""),
    ).strip(" .") or "ACE Patch"
    return "ACE Patch - " + safe_name


def write_preview(
    project_service,
    services,
    preview: ProjectPatchPreview,
    directory,
    *,
    forest,
    asset_rules=(),
    allow_write: bool = False,
) -> PreviewWriteResult:
    """Записать включённые элементы предпросмотра в патч-мод.

    ``allow_write`` — второй, нижний рубеж feature flag: use case не
    пишет, пока вызывающий явно не разрешил запись.
    """
    if not allow_write:
        raise ProjectError(
            "Запись патчей отключена: доступен только предпросмотр."
        )
    if forest is None or not str(getattr(forest, "scan_dir", "")):
        raise ProjectError("Нет построенного дерева.")
    if preview.project is not project_service.active:
        raise ProjectError("Активный проект изменился: патч не записан.")

    included = [
        item for item in preview.items
        if item.is_patch and not item.excluded
    ]
    if not included:
        raise ProjectError(
            "Все файлы исключены из патча: записывать нечего."
        )

    mod_dir = Path(directory) / _patch_mod_name(preview.project.name)
    written = []
    skipped = []

    for item in preview.items:
        if not item.is_patch:
            continue
        if item.excluded:
            continue
        if not item.ok:
            skipped.append(item.title)
            continue

        plan = item.plan
        if plan.needs_confirmation:
            plan = replace(plan, confirmed=True)
        generated = replace(item.generated, plan=plan)

        try:
            paths = services.write_patch(
                generated,
                mod_dir,
                forbidden_roots=[Path(forest.scan_dir)],
                overwrite=False,
                asset_rules=asset_rules,
            )
        except Exception as exc:
            raise ProjectError(
                "Не удалось записать %s:\n%s" % (item.title, exc)
            ) from exc
        written.extend(paths)

    if not written:
        raise ProjectError("Патчи не записаны.")

    excluded_count = sum(
        1 for item in preview.items
        if item.is_patch and item.excluded
    )
    return PreviewWriteResult(
        mod_dir=mod_dir,
        written=tuple(written),
        skipped=tuple(skipped),
        excluded_count=excluded_count,
    )


__all__ = ["PreviewWriteResult", "write_preview"]
