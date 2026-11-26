# -*- coding: utf-8 -*-
"""Внутренний композиционный корень ядра.

Не публичный порт и не API для модулей. Собирает узкие сервисы ядра в
один объект, который передаётся только composition root и платформенным
адаптерам.
"""

from __future__ import annotations

from pathlib import Path

from core.localization import LocalizationService
from core.semantic.query import SectionQueryService

from core.services.cache_control import CacheControl
from core.services.data import DataServices
from core.services.graph import GraphServices
from core.services.patch import PatchServices
from core.services.prebuild import PrebuildServices
from core.services.resolve import ResolveServices
from core.services.tree import TreeServices


class CoreComposition:
    """Композиция узких сервисов ядра.

    Атрибуты — компоненты, а не десятки делегирующих методов. Потребители
    используют ``tree``, ``resolve``, ``data``, ``patch``, ``prebuild`` и
    ``graph`` напрямую.
    """

    def __init__(
        self,
        tree_builder,
        tree_query,
        graph_builder,
        resolver_service,
        assembly_service,
        generated_dir,
        xml_resolve_service=None,
        cache_service=None,
        snapshot_service=None,
    ):
        self.cache_service = cache_service
        self.snapshot_service = snapshot_service
        self.generated_dir = Path(generated_dir)
        self.section_graph_contributors = []

        xml_resolve_service = xml_resolve_service or _default_xml_service()

        localization_cache_dir = (
            self.generated_dir / "cache-v2" / "localization"
        )
        localization = LocalizationService(
            cache_dir=localization_cache_dir,
            max_disk_bytes=256_000_000,
            cache_service=cache_service,
        )

        self.resolve = ResolveServices(
            resolver_service,
            assembly_service,
            xml_resolve_service,
            SectionQueryService(resolver_service),
        )
        self.data = DataServices(localization)
        self.prebuild = PrebuildServices(
            resolver_service,
            xml_resolve_service,
            cache_service=cache_service,
        )
        self.graph = GraphServices(
            graph_builder,
            resolver_service,
            xml_resolve_service,
        )
        self.patch = PatchServices(
            resolver_service,
            xml_resolve_service,
        )
        self.cache_control = CacheControl(
            cache_service,
            self.resolve,
            self.graph,
            self.data,
            self.prebuild,
        )
        self.tree = TreeServices(
            tree_builder,
            tree_query,
            snapshot_service=snapshot_service,
            cache_service=cache_service,
            cache_control=self.cache_control,
        )


def _default_xml_service():
    from core.xml.service import XmlResolveService

    return XmlResolveService()


__all__ = ["CoreComposition"]
