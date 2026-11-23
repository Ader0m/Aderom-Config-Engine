# -*- coding: utf-8 -*-
"""Патчи: контракт предложения изменений без фиксации."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .models import PatchTargetInfo


class PatchError(RuntimeError):
    """Ошибка предложения патча."""


class PatchUnavailableError(PatchError):
    """Движок патчей ещё не предоставлен (Этап 5)."""


@dataclass(frozen=True)
class PatchRequest:
    """Запрос модуля предложить изменение."""

    target: Path
    summary: str = ""
    details: tuple = field(default_factory=tuple)


@dataclass(frozen=True)
class PatchProposal:
    """Результат предложения: фиксация здесь невозможна."""

    accepted: bool
    reason: str = ""
    targets: tuple = field(default_factory=tuple)


class PatchApi(Protocol):
    """Предложение патча. Модуль не может зафиксировать его сам."""

    def propose(self, request: PatchRequest) -> PatchProposal:
        """Проверить и зарегистрировать предложение."""


__all__ = [
    "PatchApi",
    "PatchError",
    "PatchProposal",
    "PatchRequest",
    "PatchTargetInfo",
    "PatchUnavailableError",
]
