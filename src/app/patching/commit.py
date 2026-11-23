# -*- coding: utf-8 -*-
"""Запись предпросмотра через единственную точку записи.

Сценарий не знает про Qt: UI выбирает каталог, подтверждает опасные
изменения и показывает сообщения. Автоподтверждения нет: каждый
опасный план требует явного набора подтверждённых рисков.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

from core.api.patches import PatchCommitResult

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
    audit: Tuple[PatchCommitResult, ...] = ()


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
    confirmed_risks=(),
    log=None,
    cancel_check=None,
    snapshot_provider=None,
) -> PreviewWriteResult:
    """Записать включённые элементы предпросмотра в патч-мод.

    ``allow_write`` — второй, нижний рубеж feature flag: use case не
    пишет, пока вызывающий явно не разрешил запись.
    ``confirmed_risks`` — явно подтверждённые ключи опасных изменений;
    без них ``PatchCommitService`` не выдаст авторизацию.
    """
    def cancelled():
        return bool(cancel_check is not None and cancel_check())

    if not allow_write:
        raise ProjectError(
            "Запись патчей отключена: доступен только предпросмотр."
        )
    if cancelled():
        raise ProjectError("Запись патч-мода отменена.")
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

    def current_snapshot():
        if snapshot_provider is not None:
            return str(snapshot_provider() or "")
        return str(getattr(forest, "cache_id", "") or "")

    current_snapshot_id = current_snapshot()
    if not current_snapshot_id:
        raise ProjectError(
            "Снимок VFS не определён: запись патча невозможна."
        )
    for item in included:
        if not item.ok:
            continue
        if item.generated is None:
            raise ProjectError(
                "Предпросмотр устарел: нет сгенерированного патча."
            )
        if str(item.generated.plan.snapshot_id or "") != current_snapshot_id:
            raise ProjectError(
                "Снимок VFS изменился: патч не записан."
            )

    def remove_paths(paths):
        for path in reversed(tuple(paths)):
            try:
                path = Path(path)
                if path.exists():
                    path.unlink()
            except OSError:
                pass

    mod_dir = Path(directory) / _patch_mod_name(preview.project.name)
    written = []
    skipped = []
    audit = []
    pending = [
        item for item in preview.items
        if item.is_patch and not item.excluded and item.ok
    ]
    index = 0
    try:
        for item in preview.items:
            if not item.is_patch:
                continue
            if item.excluded:
                continue
            if not item.ok:
                skipped.append(item.title)
                continue
            if cancelled():
                raise ProjectError("Запись патч-мода отменена.")
            index += 1
            if log:
                log(
                    "Патч: запись %d/%d — %s"
                    % (index, len(pending), item.title)
                )

            plan = item.generated.plan
            current = current_snapshot()
            if current != current_snapshot_id or str(
                plan.snapshot_id or ""
            ) != current:
                raise ProjectError(
                    "Снимок VFS изменился: патч не записан."
                )

            try:
                authorization = services.patch_commit.authorize(
                    item.generated,
                    confirmed_risks=confirmed_risks,
                    roundtrip_report=item.roundtrip,
                )
                if cancelled():
                    raise ProjectError("Запись патч-мода отменена.")
                current = current_snapshot()
                if current != current_snapshot_id or str(
                    plan.snapshot_id or ""
                ) != current:
                    raise ProjectError(
                        "Снимок VFS изменился: патч не записан."
                    )
                result = services.patch_commit.commit(
                    item.generated,
                    authorization,
                    mod_dir,
                    expected_snapshot_id=current,
                    forbidden_roots=[Path(forest.scan_dir)],
                    overwrite=False,
                    asset_rules=asset_rules,
                )
            except ProjectError:
                raise
            except Exception as exc:
                raise ProjectError(
                    "Не удалось записать %s:\n%s" % (item.title, exc)
                ) from exc

            after = current_snapshot()
            if cancelled():
                services.patch_commit.audit_outcome(
                    result,
                    "cancelled",
                    "запись отменена",
                )
                remove_paths(result.paths)
                raise ProjectError("Запись патч-мода отменена.")
            if after != current or str(plan.snapshot_id or "") != after:
                services.patch_commit.audit_outcome(
                    result,
                    "rolled_back",
                    "снимок VFS изменился",
                )
                remove_paths(result.paths)
                raise ProjectError(
                    "Снимок VFS изменился: патч не записан."
                )

            written.extend(result.paths)
            audit.append(result)
            if log:
                log(
                    "Патч: записано файлов %d — %s"
                    % (len(result.paths), item.title)
                )
    except Exception as exc:
        # Транзакция use case: если поздний элемент не записался,
        # удаляем всё, что успели записать в этот патч-мод и
        # фиксируем откат в аудите.
        for record in audit:
            try:
                services.patch_commit.audit_outcome(
                    record,
                    "rolled_back",
                    str(exc),
                )
            except Exception:
                pass
        for path in reversed(written):
            try:
                path = Path(path)
                if path.exists():
                    path.unlink()
            except OSError:
                pass
        raise

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
        audit=tuple(audit),
    )


__all__ = ["PreviewWriteResult", "write_preview"]
