# -*- coding: utf-8 -*-
"""Намерение изменения, снимок и итог прохода патч-движка.

Модели не пишут файлы и не знают о UI:

- :class:`PatchIntent` — что именно нужно изменить;
- :class:`PatchSnapshot` — контекст снимка, по которому строится план;
- :class:`PatchBundle` — план, сгенерированный патч и проверка.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from core.patching.models import (
    GeneratedPatch,
    PatchOptions,
    PatchPlan,
    VerificationResult,
)


PATCH_INTENT_KINDS = ("ltx", "xml", "asset", "new_file")


@dataclass(frozen=True)
class PatchIntent:
    """Семантическое намерение изменения.

    ``kind`` выбирает движок. Поля ``before_text``/``after_text``/``mode``
    относятся к LTX/XML; ``relative_path``/``asset_*`` — к asset/new_file.
    Намерение не содержит открытых дескрипторов и не пишет файлы.
    """

    kind: str
    source_path: Path
    # Для LTX/XML непустое значение переопределяет PatchOptions.patch_mod;
    # для asset/new_file — имя патч-мода (пустое — options.patch_mod).
    patch_mod: str = ""
    options: Optional[PatchOptions] = None

    # LTX / XML
    before_text: str = ""
    after_text: str = ""
    mode: str = ""
    line_sources: Tuple[object, ...] = ()
    resolved: bool = False
    analysis: Optional[object] = None
    project_mode: str = "root"
    overlay: Optional[object] = None
    verify_resolver: bool = True

    # asset / new_file
    relative_path: Optional[Path] = None
    patch_root: Optional[Path] = None
    vfs_root: str = ""
    asset_save_mode: str = ""
    asset_data: Optional[bytes] = None
    changes: Tuple[object, ...] = ()

    def __post_init__(self):
        if self.kind not in PATCH_INTENT_KINDS:
            raise ValueError("Неизвестный вид патча: %s" % self.kind)
        if self.source_path is None:
            raise ValueError("Не задан источник патча")


@dataclass(frozen=True)
class PatchSnapshot:
    """Контекст снимка, по которому строится план.

    Непустой ``snapshot_id`` — источник истины: LTX/XML переносят его в
    ``PatchOptions.snapshot_id``, asset/new_file — напрямую в
    ``PatchPlan.snapshot_id``. Если снимок пуст, используется непустой
    ``PatchOptions.snapshot_id``. Непустой несовпадающий
    ``PatchOptions.snapshot_id`` отклоняется.
    """

    forest: object = None
    overlay: object = None
    snapshot_id: str = ""


@dataclass(frozen=True)
class PatchBundle:
    """Итог прохода движка: намерение, план, патч и проверка."""

    intent: PatchIntent
    snapshot: PatchSnapshot
    plan: PatchPlan
    generated: Optional[GeneratedPatch] = None
    verification: Optional[VerificationResult] = None
    notes: Tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return (
            not self.plan.has_errors
            and self.generated is not None
            and bool(self.generated.files)
            and self.verification is not None
            and self.verification.ok
        )


__all__ = [
    "PATCH_INTENT_KINDS",
    "PatchBundle",
    "PatchIntent",
    "PatchSnapshot",
]
