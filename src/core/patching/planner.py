# -*- coding: utf-8 -*-
"""Dispatchers планирования патчей.

Форматная логика живёт в:

- :mod:`core.patching.ltx.planner`;
- :mod:`core.patching.xml.planner`.

``PatchPlanner`` сохраняет прежний публичный API и делегирует вызовы
соответствующему планировщику.
"""

from __future__ import annotations

from typing import Optional

from core.patching.ltx.generator import DltxGenerator
from core.patching.ltx.planner import LtxPlanner
from core.patching.models import (
    PatchOptions,
    PatchPlan,
    PatchPlanningError,
)
from core.patching.xml.generator import DxmlGenerator
from core.patching.xml.planner import XmlPlanner


class PatchPlanner:
    """Планировщик патчей с раздельными LTX/XML реализациями."""

    def __init__(self, resolver_adapter=None, cache=None):
        self.resolver_adapter = resolver_adapter
        self.cache = cache
        self.ltx = LtxPlanner(resolver_adapter, cache)
        self.xml = XmlPlanner(resolver_adapter, cache)

    # -------------------------------------------------- XML
    def plan_xml_document_change(
        self,
        source_path,
        baseline_text: str,
        draft_text: str,
        forest=None,
        options: Optional[PatchOptions] = None,
    ) -> PatchPlan:
        """Построить план изменения сырого XML-файла."""
        return self.xml.plan_xml_document_change(
            source_path,
            baseline_text,
            draft_text,
            forest=forest,
            options=options,
        )

    def plan_xml_resolved_edit(
        self,
        source_path,
        baseline_text: str,
        edited_text: str,
        line_sources=(),
        forest=None,
        options: Optional[PatchOptions] = None,
    ) -> PatchPlan:
        """Построить план правки resolved-вида XML (DXML)."""
        return self.xml.plan_xml_resolved_edit(
            source_path,
            baseline_text,
            edited_text,
            line_sources=line_sources,
            forest=forest,
            options=options,
        )

    # -------------------------------------------------- LTX
    def plan_document_change(
        self,
        source_path,
        baseline_text: str,
        draft_text: str,
        forest=None,
        options: Optional[PatchOptions] = None,
    ) -> PatchPlan:
        """Построить план по тексту оригинала и черновика."""
        return self.ltx.plan_document_change(
            source_path,
            baseline_text,
            draft_text,
            forest=forest,
            options=options,
        )

    def plan_single_line_edit(
        self,
        source_path,
        baseline_text,
        edited_text,
        line_sources=(),
        forest=None,
        options: Optional[PatchOptions] = None,
    ) -> PatchPlan:
        """Построить патч по ровно одной изменённой строке."""
        return self.ltx.plan_single_line_edit(
            source_path,
            baseline_text,
            edited_text,
            line_sources=line_sources,
            forest=forest,
            options=options,
        )

    def plan_multiline_edit(
        self,
        source_path,
        baseline_text,
        edited_text,
        line_sources=(),
        forest=None,
        options: Optional[PatchOptions] = None,
        analysis=None,
    ) -> PatchPlan:
        """Построить один патч по всем изменениям одного файла."""
        return self.ltx.plan_multiline_edit(
            source_path,
            baseline_text,
            edited_text,
            line_sources=line_sources,
            forest=forest,
            options=options,
            analysis=analysis,
        )

    def plan_text_patch(
        self,
        original_path,
        desired_text,
        target_mod=None,
    ):
        """Сравнить файл с желаемым текстом."""
        return self.ltx.plan_text_patch(
            original_path,
            desired_text,
            target_mod=target_mod,
        )

    def plan_property_patch(
        self,
        original_path,
        section_name,
        key_name,
        desired_value,
        target_mod=None,
        forest=None,
        delete=False,
        options: Optional[PatchOptions] = None,
    ):
        """Построить план изменения одного ключа."""
        return self.ltx.plan_property_patch(
            original_path,
            section_name,
            key_name,
            desired_value,
            target_mod=target_mod,
            forest=forest,
            delete=delete,
            options=options,
        )

    def preview(self, plan: PatchPlan) -> str:
        if plan.is_xml:
            return plan.preview or DxmlGenerator().preview(plan)
        return plan.preview or DltxGenerator().preview(plan)


__all__ = ["PatchPlanner", "PatchPlanningError"]
