# -*- coding: utf-8 -*-
"""Сводка по построенному дереву для платформенных сценариев.

``LtxForest`` — внутренняя модель ядра, а ``TreeSummary`` — публичная
модель SDK. Платформа строит сводку в одном месте, чтобы UI и мост не
дублировали правила подсчёта и не зависели от метода, которого нет у
``CoreComposition``.
"""

from __future__ import annotations

from sdk.contracts.models import TreeSummary


def summarize_forest(forest) -> TreeSummary:
    """Построить ``TreeSummary`` по сырому или SDK-лесу."""
    resolver_sections = getattr(forest, "resolver_sections", None)
    if resolver_sections is not None:
        try:
            resolver_sections = int(resolver_sections)
        except (TypeError, ValueError):
            pass

    return TreeSummary(
        roots=len(getattr(forest, "roots", ()) or ()),
        total_files=int(getattr(forest, "total_files", 0) or 0),
        system_tree_files=int(getattr(forest, "system_tree_files", 0) or 0),
        resolver_sections=resolver_sections,
        xml_files=int(
            getattr(forest, "total_xml_files", 0)
            or len(getattr(forest, "xml_files", ()) or ())
        ),
    )
