# -*- coding: utf-8 -*-
"""Capability ``section.graph``: граф связей секции."""

from __future__ import annotations

from typing import Protocol

from sdk.contracts.models import SectionGraphRequest


class SectionGraphApi(Protocol):
    """Контракт capability ``section.graph`` (провайдер — ``base``)."""

    def register_contributor(self, contributor):
        """Подключить contributor графа; вернуть handle отписки."""

    def unregister_contributor(self, handle) -> None:
        """Отключить ранее подключённый contributor."""

    def show(self) -> None:
        """Открыть/сфокусировать окно графа."""

    def request(self, request: SectionGraphRequest):
        """Построить граф по запросу (без UI-команд)."""
