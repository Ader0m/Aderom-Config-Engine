# -*- coding: utf-8 -*-
"""Композиционный адаптер ядра для composition root.

Класс ``CoreServices`` остаётся в ``core.facade`` до завершения Этапа 9;
этот порт даёт прикладному слою и composition root явную точку входа.
"""

from core.facade import CoreServices

__all__ = ["CoreServices"]
