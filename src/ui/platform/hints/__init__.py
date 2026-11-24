# -*- coding: utf-8 -*-
"""Платформенный сервис подсказок.

Каталоги, провайдеры и реестр принадлежат платформе. UI-часть движка
(popup, контроллер и адаптеры) остаётся в ``ui.hints``.
"""

from .service import HintCatalogs, HintService

__all__ = ["HintCatalogs", "HintService"]
