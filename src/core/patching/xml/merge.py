# -*- coding: utf-8 -*-
"""Сборка одного XML-патча проекта из поштучных планов.

Функция не знает про UI и ``PreviewItem``. Прикладной слой передаёт
тройки ``(plan, generated, source_path)`` и получает объединённый план и
готовый ``GeneratedPatch``.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Iterable, Optional, Tuple

from core.patching.models import (
    GeneratedPatch,
    PatchIssue,
    PatchPlan,
    PatchPlanningError,
    VerificationResult,
)


def merge_xml_generated(
    services,
    entries: Iterable[
        Tuple[PatchPlan, Optional[GeneratedPatch], Path]
    ],
) -> Tuple[PatchPlan, GeneratedPatch]:
    """Собрать XML-патч проекта: копии файлов + один файл добавлений.

    ``entries`` — последовательность ``(plan, generated, source_path)``.
    ``generated`` может быть ``None``: тогда патч генерируется через
    ``services.generate_patch``. Готовый XML-форк передаётся как есть.
    """
    entries = tuple(entries)
    if not entries:
        raise PatchPlanningError("Нет XML-планов для объединения")

    generated_items = []
    changes = []
    issues = []
    warnings = []
    checks = []
    all_ok = True
    modes = {}

    for plan, generated, source_path in entries:
        changes.extend(plan.changes)
        issues.extend(plan.issues)
        warnings.extend(plan.warnings)

        source_key = str(source_path).lower()
        mode = plan.xml_mode
        if source_key in modes and modes[source_key] != mode:
            issues.append(
                PatchIssue(
                    code="xml_mode_conflict",
                    message=(
                        "Один XML-файл изменён и как сырой, и как "
                        "resolved: выберите один режим, иначе патчи "
                        "могут противоречить друг другу"
                    ),
                    severity="error",
                )
            )
            all_ok = False
        modes[source_key] = mode

        if not plan.can_generate:
            all_ok = False
            continue

        if (
            getattr(plan, "format", "") == "xml_fork"
            and generated is not None
        ):
            current = generated
        else:
            current = services.generate_patch(plan)
        if current.verification is None or not current.verification.ok:
            all_ok = False
        if current.verification is not None:
            checks.extend(current.verification.checks)
            issues.extend(current.verification.issues)
        generated_items.append((plan, current))

    merged_plan = replace(
        entries[0][0],
        changes=tuple(changes),
        issues=tuple(issues),
        warnings=tuple(warnings),
        preview="",
        diff=None,
    )

    files = []
    for _plan, generated in generated_items:
        for file in generated.files:
            if Path(file.relative_path).suffix.lower() == ".ltx":
                # DLTX перепривязок уже объединён с патчем своего
                # корня: второй файл с тем же именем писать нельзя.
                continue
            files.append(file)

    preview_parts = []
    for file in files:
        preview_parts.append("-- Файл: %s" % file.relative_path)
        preview_parts.append(file.text.rstrip())
        preview_parts.append("")
    preview = "\n".join(preview_parts)

    verification = VerificationResult(
        ok=all_ok and bool(files),
        checks=tuple(checks),
        issues=tuple(issues),
    )
    generated = GeneratedPatch(
        plan=merged_plan,
        files=tuple(files),
        text=preview,
        verification=verification,
    )
    return merged_plan, generated


__all__ = ["merge_xml_generated"]
