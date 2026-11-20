# -*- coding: utf-8 -*-
"""Единый вход планирования LTX-патчей.

Режимы:

- ``root`` — DLTX-дельта для корня открытого документа;
- ``on_file`` — то же планирование на один документ; отдельные патч-файлы
  на каждый изменённый документ собирает уровень проекта;
- ``override`` — полная копия файла-победителя.

Вход совпадает по форме с :class:`core.patching.xml.service.XmlPatchService`.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Optional

from core.patching.ltx.generator import DltxGenerator
from core.patching.ltx.override import LtxOverrideService
from core.patching.ltx.verifier import ModelPatchVerifier
from core.patching.models import PatchOptions, PatchResult
from core.patching.planner import PatchPlanner


class LtxPatchService:
    """Планирование LTX-патча в одном из режимов."""

    def __init__(self, services=None):
        self.services = services
        self.planner = PatchPlanner()
        self.override = LtxOverrideService()

    def plan(
        self,
        source_path,
        before_text: str,
        after_text: str,
        *,
        mode: str = "root",
        line_sources=(),
        forest=None,
        options: Optional[PatchOptions] = None,
        resolved: bool = False,
        analysis=None,
        verify_resolver: bool = True,
    ) -> PatchResult:
        mode = (mode or "root").lower().replace("-", "_")
        if mode == "in_place":
            # Старое имя: патчили файл-победитель строки, что для DLTX
            # неверно. Новый режим — on_file, но цель — корень загрузки.
            mode = "on_file"
        options = options or PatchOptions()
        source_path = Path(source_path)

        if mode == "override":
            result = self.override.plan(
                source_path,
                before_text,
                after_text,
                line_sources=line_sources,
                forest=forest,
                options=options,
                resolved=resolved,
            )
            return PatchResult(
                plan=result.plan,
                generated=result.generated,
                notes=tuple(
                    issue.message for issue in result.plan.warnings
                ),
            )

        if mode not in ("root", "on_file"):
            raise ValueError("Неизвестный режим LTX-патча: %s" % mode)

        if resolved:
            plan = self.planner.plan_multiline_edit(
                source_path,
                before_text,
                after_text,
                line_sources=line_sources,
                forest=forest,
                options=options,
                analysis=analysis,
            )
        else:
            plan = self.planner.plan_document_change(
                source_path,
                before_text,
                after_text,
                forest,
                options,
            )

        generated = None
        if plan.can_generate:
            generated = self._generate(
                plan,
                verify_resolver=verify_resolver,
            )

        return PatchResult(
            plan=plan,
            generated=generated,
            notes=tuple(issue.message for issue in plan.warnings),
        )

    def _generate(self, plan, verify_resolver=True):
        """Сгенерировать патч одного элемента.

        ``verify_resolver=False`` нужен уровню проекта в режиме Root: там
        поштучный патч всё равно заменяется объединённым, и полный проход
        Resolver4 по каждому файлу — чистая потеря времени. Модельная
        round-trip проверка остаётся: она дешёвая и ловит ошибки
        наследования и операций со списками, которые resolver-проверка
        объединённого плана не покрывает.
        """
        if verify_resolver and self.services is not None and hasattr(
            self.services,
            "generate_patch",
        ):
            return self.services.generate_patch(plan)

        generated = DltxGenerator().generate(plan)
        if plan.diff is None:
            # Модельной проверке нужен исходный diff; для таких планов
            # оставляем патч без вердикта, а не помечаем его ошибкой.
            return generated
        verification = ModelPatchVerifier().verify(generated)
        return replace(generated, verification=verification)
