# -*- coding: utf-8 -*-
"""DTO предложения патча. Фиксация патча модулю недоступна."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class PatchTargetInfo:
    """Цель патча и все кандидаты, если корень неоднозначен."""

    root_path: Optional[Path]
    candidates: tuple[Path, ...] = field(default_factory=tuple)
    reason: str = ""
    ambiguous: bool = False
