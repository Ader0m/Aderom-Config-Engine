# -*- coding: utf-8 -*-
"""PatchApiAdapter: предложение патча без фиксации.

До Этапа 5 движок патчей не предоставлен. Контракт существует, но
``propose`` явно сообщает об этом и ничего не пишет.
"""

from __future__ import annotations

from sdk.features.patches import (
    PatchProposal,
    PatchUnavailableError,
)


class PatchApiAdapter:
    """Реализация :class:`sdk.features.patches.PatchApi`."""

    def __init__(self, service=None):
        self._service = service

    def propose(self, intent) -> PatchProposal:
        if self._service is None:
            raise PatchUnavailableError(
                "Движок патчей будет предоставлен на Этапе 5"
            )
        return self._service.propose(intent)
