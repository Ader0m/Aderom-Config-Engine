# -*- coding: utf-8 -*-
"""Единый вход планирования XML-патчей.

Режимы:

- ``auto`` / ``fork`` / ``xml`` — форк ключа и перепривязка ссылок;
- ``full_copy`` / ``xml_override`` — полная копия XML-файла;
- ``resolved`` / ``dxml_lua`` — DXML-Lua-патч для resolved-вида.

Вход совпадает по форме с :class:`core.patching.ltx.service.LtxPatchService`.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Optional

from core.patching.models import PatchOptions, PatchResult
from core.patching.planner import PatchPlanner
from core.patching.xml.generator import DxmlGenerator
from core.patching.xml.fork import XmlForkService
from core.patching.xml.verifier import XmlModelPatchVerifier


class _DefaultServices:
    """Минимальный набор зависимостей для XmlForkService без фасада."""

    def __init__(self):
        self.patch = PatchPlanner()

    def generate_patch(self, plan):
        generated = DxmlGenerator().generate(plan)
        verification = XmlModelPatchVerifier().verify(generated)
        return replace(generated, verification=verification)


class XmlPatchService:
    """Планирование XML/DXML-патча в одном из режимов."""

    def __init__(self, services=None):
        self.services = services or _DefaultServices()
        self.planner = PatchPlanner()
        self.fork = XmlForkService(self.services)

    def plan(
        self,
        source_path,
        before_text: str,
        after_text: str,
        *,
        mode: str = "auto",
        line_sources=(),
        forest=None,
        options: Optional[PatchOptions] = None,
        project_mode: Optional[str] = None,
    ) -> PatchResult:
        mode = (mode or "auto").lower().replace("-", "_")
        options = options or PatchOptions()
        source_path = Path(source_path)

        if mode in ("auto", "fork", "xml"):
            result = self.fork.plan(
                source_path,
                before_text,
                after_text,
                forest=forest,
                options=options,
                project_mode=project_mode or "root",
            )
            return PatchResult(
                plan=result.plan,
                generated=result.generated,
                notes=tuple(
                    issue.message for issue in result.plan.warnings
                ),
                plans=tuple(result.ltx_plans),
                keys=result.keys,
                references=result.references,
            )

        if mode in ("full_copy", "xml_override"):
            plan = self.planner.plan_xml_document_change(
                source_path,
                before_text,
                after_text,
                forest=forest,
                options=options,
            )
        elif mode in ("resolved", "dxml_lua"):
            plan = self.planner.plan_xml_resolved_edit(
                source_path,
                before_text,
                after_text,
                line_sources=line_sources,
                forest=forest,
                options=options,
            )
        else:
            raise ValueError("Неизвестный режим XML-патча: %s" % mode)

        generated = None
        if plan.can_generate:
            generated = self.services.generate_patch(plan)

        return PatchResult(
            plan=plan,
            generated=generated,
            notes=tuple(issue.message for issue in plan.warnings),
        )
