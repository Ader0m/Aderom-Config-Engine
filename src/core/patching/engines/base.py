# -*- coding: utf-8 -*-
"""Базовый контракт форматного патч-движка.

Движок планирует, генерирует и проверяет. Запись остаётся отдельной
операцией :class:`core.patching.commit.PatchCommitService`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace
from pathlib import Path

from core.patching.intent import PatchIntent, PatchSnapshot
from core.patching.models import (
    GeneratedPatch,
    PatchOptions,
    PatchPlan,
    PatchPlanningError,
    PatchResult,
    VerificationResult,
)


class PatchEngine(ABC):
    """Один формат патча."""

    kind = ""

    @abstractmethod
    def plan(
        self,
        intent: PatchIntent,
        snapshot: PatchSnapshot,
    ) -> PatchResult:
        """Построить план и, если возможно, сразу сгенерировать патч."""

    @abstractmethod
    def generate(self, plan: PatchPlan) -> GeneratedPatch:
        """Сгенерировать патч по плану."""

    @abstractmethod
    def verify(self, generated: GeneratedPatch) -> VerificationResult:
        """Проверить сгенерированный патч."""

    @abstractmethod
    def preview(self, plan: PatchPlan) -> str:
        """Текстовый предпросмотр плана."""


def validate_snapshot(
    intent: PatchIntent,
    snapshot: PatchSnapshot,
) -> None:
    """Не разрешать несовпадение снимков у намерения и снимка."""
    options = intent.options
    if options is None:
        return
    snapshot_id = str(snapshot.snapshot_id or "")
    if not snapshot_id or not options.snapshot_id:
        return
    if options.snapshot_id != snapshot_id:
        raise PatchPlanningError(
            "Снимок плана не совпадает: options.snapshot_id=%s, "
            "snapshot.snapshot_id=%s"
            % (options.snapshot_id, snapshot_id)
        )


def snapshot_id_for_intent(
    intent: PatchIntent,
    snapshot: PatchSnapshot,
) -> str:
    """Единый выбор ``snapshot_id`` для всех движков.

    Приоритет:

    1. ``PatchSnapshot.snapshot_id``, если он не пуст;
    2. ``PatchOptions.snapshot_id``, если снимок пуст;
    3. пустая строка.

    Непустой несовпадающий ``PatchOptions.snapshot_id`` — ошибка.
    """
    validate_snapshot(intent, snapshot)
    snapshot_id = str(snapshot.snapshot_id or "")
    if snapshot_id:
        return snapshot_id
    if intent.options is not None:
        return str(intent.options.snapshot_id or "")
    return ""


def options_for_intent(
    intent: PatchIntent,
    snapshot: PatchSnapshot,
) -> PatchOptions:
    """Собрать параметры плана с единым источником истины.

    Приоритет:

    1. ``PatchSnapshot.snapshot_id`` — если задан, он переносится в
       ``PatchOptions.snapshot_id``. Непустой несовпадающий
       ``PatchOptions.snapshot_id`` — ошибка, а не молчаливая подмена.
    2. ``PatchIntent.patch_mod`` — если задан, переопределяет
       ``PatchOptions.patch_mod``.
    """
    options = intent.options or PatchOptions()
    snapshot_id = snapshot_id_for_intent(intent, snapshot)
    if snapshot_id and options.snapshot_id != snapshot_id:
        options = replace(options, snapshot_id=snapshot_id)

    patch_mod = str(intent.patch_mod or "")
    if patch_mod and options.patch_mod != patch_mod:
        options = replace(options, patch_mod=patch_mod)
    return options


def patch_mod_for_intent(intent: PatchIntent) -> str:
    """Имя патч-мода: intent переопределяет options, иначе default."""
    value = str(intent.patch_mod or "")
    if value:
        return value
    if intent.options is not None:
        return intent.options.patch_mod
    return PatchOptions().patch_mod


def combine_verification(
    model: VerificationResult,
    resolver: VerificationResult,
) -> VerificationResult:
    """Объединить модельную и resolver-проверки в один результат."""
    return VerificationResult(
        ok=model.ok and resolver.ok,
        checks=tuple(model.checks) + tuple(resolver.checks),
        issues=tuple(model.issues) + tuple(resolver.issues),
    )


def require_relative_path(relative, label: str) -> Path:
    """Проверить относительный путь внутри патч-мода."""
    if relative is None or not str(relative).strip():
        raise PatchPlanningError("Не задан путь %s" % label)
    path = Path(relative)
    if str(path) in ("", "."):
        raise PatchPlanningError("Не задан путь %s" % label)
    if path.is_absolute() or ".." in path.parts:
        raise PatchPlanningError(
            "Путь %s выходит за пределы мода: %s" % (label, path)
        )
    return path


__all__ = [
    "PatchEngine",
    "combine_verification",
    "options_for_intent",
    "patch_mod_for_intent",
    "require_relative_path",
    "snapshot_id_for_intent",
    "validate_snapshot",
]
