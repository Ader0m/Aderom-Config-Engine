# -*- coding: utf-8 -*-
"""DTO предпросмотра и записи патча.

Эти модели принадлежат патч-сценарию, а не проекту: ``PreviewItem`` и
``ProjectPatchPreview`` — временная проекция предпросмотра, а не данные
``project.json``. ``app.projects.models`` намеренно не импортирует
патч-модели (DATA-4).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from core.api.patches import (
    GeneratedPatch,
    PatchPlan,
    RoundTripReport,
)

from app.projects.models import (
    Project,
    ProjectDraft,
    ProjectResolvedDraft,
)


@dataclass(frozen=True)
class AssetChange:
    """Изменение двоичного ресурса (для PatchPlan.changes)."""

    kind: str
    path: Path
    description: str
    risk: str = "safe"


@dataclass(frozen=True)
class PreviewItem:
    """Один файл проекта в предпросмотре патча."""

    title: str
    source_path: Path
    plan: Optional[PatchPlan]
    generated: Optional[GeneratedPatch]
    error: str = ""
    kind: str = "file"
    draft: Optional[ProjectDraft] = None
    resolved: Optional[ProjectResolvedDraft] = None
    notes: Tuple[str, ...] = ()
    text: str = ""
    # Полный round-trip, полученный в фоне до записи. None — ещё не
    # проверялся; неуспешный отчёт делает элемент неготовым к записи.
    roundtrip: Optional[RoundTripReport] = None
    # DLTX-планы, которые несёт XML-форк: уровень проекта в режиме Root
    # объединяет их с планами того же корня.
    plans: Tuple[PatchPlan, ...] = ()
    # Файл исключён из записи в патч-мод. Черновик и правки проекта при
    # этом не меняются: исключение действует только на этот предпросмотр.
    excluded: bool = False
    # Что откатывает «Откатить изменения»: черновики этих путей и ключи
    # resolved-черновиков. Для Root/XML-строк это объединённый набор
    # всех файлов, из которых собрана строка.
    rollback_paths: Tuple[Path, ...] = ()
    rollback_resolved_ids: Tuple[str, ...] = ()

    @property
    def can_rollback(self) -> bool:
        return bool(self.rollback_paths or self.rollback_resolved_ids)

    @property
    def is_patch(self) -> bool:
        return self.kind not in ("info", "script")

    @property
    def ok(self) -> bool:
        if not self.is_patch:
            return not self.error
        if self.error:
            return False
        if self.plan is None or self.generated is None:
            return False
        if self.roundtrip is not None and not self.roundtrip.ok:
            return False
        return bool(
            self.generated.verification
            and self.generated.verification.ok
        )


@dataclass(frozen=True)
class ProjectPatchPreview:
    """Результат подготовки патча по проекту."""

    project: Project
    items: Tuple[PreviewItem, ...] = ()

    @property
    def ok_items(self) -> Tuple[PreviewItem, ...]:
        return tuple(
            item for item in self.items
            if item.is_patch and item.ok
        )

    @property
    def included_items(self) -> Tuple[PreviewItem, ...]:
        """Элементы, которые попадут в патч-мод (без исключённых)."""
        return tuple(item for item in self.items if not item.excluded)

    @property
    def has_errors(self) -> bool:
        return any(
            item.is_patch and not item.ok
            for item in self.items
        )

    @property
    def patch_count(self) -> int:
        return len(self.ok_items)


__all__ = [
    "AssetChange",
    "PreviewItem",
    "ProjectPatchPreview",
]
