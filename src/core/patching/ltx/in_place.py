# -*- coding: utf-8 -*-
"""Устаревший DLTX In-place: патч для файла-победителя строки.

.. deprecated::
   Для DLTX патч нельзя привязать к include-файлу: движок ищет
   ``mod_<имя>_*.ltx`` только для корней загрузки. Этот модуль оставлен
   для совместимости и не используется режимом проекта ``on_file``.
   Актуальное поведение — обычное планирование Root на каждый файл.

Режим отличается от Root тем, что не собирает изменения всех файлов
одного корня в один патч. Для каждой заменённой строки определяется
файл-победитель, и создаётся отдельный DLTX-патч рядом с этим файлом
(``mod_<победитель>_~<проект>.ltx``). Так патч остаётся в той же папке
виртуальной файловой системы, где находится изменённый файл.

Правила выбора победителя:

- строка помечена ``оригинал`` — меняется сам открытый файл;
- файл редактируется без резолва — меняется сам открытый файл;
- у строки есть владелец (include-файл или чужой DLTX-патч) — меняется
  строка в файле владельца.

Если владелец — DLTX-патч, патч создаётся для его корня загрузки: иначе
новый ``mod_mod_*.ltx`` не был бы загружен игрой.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.patching.ltx.diff import DocumentChange
from core.patching.ltx.generator import DltxGenerator
from core.patching.ltx.source import (
    find_section_key_line,
    find_winner_line,
    line_changes,
    resolve_source_path,
    value_from_new_line,
)
from core.patching.models import (
    GeneratedPatch,
    PatchIssue,
    PatchOptions,
    PatchPlan,
    PatchTarget,
    VerificationCheck,
    VerificationResult,
)
from core.patching.targets import (
    choose_load_last_suffix,
    existing_patch_names,
    patch_file_name,
    preferred_suffix,
    resolve_patch_target,
)


class LtxInPlaceError(Exception):
    """DLTX In-place нельзя построить."""


@dataclass(frozen=True)
class LtxInPlaceResult:
    """Результат In-place: один общий предпросмотр и отдельные планы."""

    plan: PatchPlan
    generated: GeneratedPatch
    plans: Tuple[PatchPlan, ...] = ()
    notes: Tuple[str, ...] = ()


def _relative_parent(forest, path: Path) -> Path:
    scan_dir = getattr(forest, "scan_dir", None)
    if not scan_dir:
        return Path()
    try:
        return Path(path).parent.relative_to(Path(scan_dir))
    except (ValueError, OSError):
        return Path()


def _target_root(winner: Path, forest) -> Tuple[Path, bool]:
    """Файл, рядом с которым создаётся DLTX-патч.

    Обычный победитель (оригинал, include-файл, файл без резолва) —
    патч создаётся рядом с ним и называется по нему. Если победитель —
    чужой DLTX-патч, используется его корень загрузки: иначе имя
    ``mod_mod_*`` не имело бы смысла.

    Возвращает ``(root, is_existing_patch)``.
    """
    if winner.name.lower().startswith("mod_"):
        try:
            target = resolve_patch_target(forest, winner)
        except Exception:
            target = None
        root = getattr(target, "root_path", None) if target is not None else None
        if root is None or Path(root) == winner:
            root = _root_from_patch_name(winner, forest) or root
        return Path(root or winner), True
    return winner, False


def _root_from_patch_name(winner: Path, forest) -> Optional[Path]:
    """Найти корень по имени чужого DLTX-патча ``mod_<root>_<suffix>.ltx``."""
    remainder = winner.stem[4:].lower()
    best = None
    for path in getattr(forest, "files", ()) or ():
        path = Path(path)
        if path.parent != winner.parent:
            continue
        if path.name.lower().startswith("mod_"):
            continue
        if remainder.startswith(path.stem.lower() + "_"):
            if best is None or len(path.stem) > len(best.stem):
                best = path
    return best


def _winner_path(source, source_path: Path, forest, config_dir) -> Optional[Path]:
    if source is None:
        return source_path

    kind = getattr(source, "kind", None)
    if kind == "original":
        return source_path

    winner = resolve_source_path(
        source,
        source_path,
        forest=forest,
        config_dir=config_dir,
    )
    if winner is None:
        path = getattr(source, "path", None)
        if path is not None and Path(path).is_file():
            winner = Path(path)
    return winner


class LtxInPlaceService:
    """Планирование DLTX-патчей для файлов-победителей строк."""

    def __init__(self, services=None):
        self.services = services

    # -------------------------------------------------- публичный вход
    def plan(
        self,
        source_path,
        baseline_text: str,
        edited_text: str,
        line_sources=(),
        forest=None,
        options: Optional[PatchOptions] = None,
        resolved: bool = False,
    ) -> LtxInPlaceResult:
        source_path = Path(source_path)
        options = options or PatchOptions()

        if source_path.suffix.lower() != ".ltx":
            raise LtxInPlaceError(
                "DLTX In-place поддерживает только .ltx: %s" % source_path
            )

        config_dir = (
            Path(getattr(forest, "scan_dir"))
            if forest is not None and getattr(forest, "scan_dir", None)
            else source_path.parent
        )

        warnings: List[PatchIssue] = []
        changes = line_changes(
            baseline_text,
            edited_text,
            () if not resolved else line_sources,
        )

        used_lines: Dict[Path, set] = {}
        grouped: Dict[
            Path,
            Dict[Tuple[str, str], DocumentChange],
        ] = {}
        notes: List[str] = []

        for change in changes:
            if change.kind != "value":
                warnings.append(
                    PatchIssue(
                        code="ltx_in_place_structural",
                        message=(
                            "Строка %d: структурная правка не поддержана "
                            "In-place" % change.line_number
                        ),
                        severity="warning",
                    )
                )
                continue

            if not change.section or not change.key:
                warnings.append(
                    PatchIssue(
                        code="ltx_in_place_no_context",
                        message=(
                            "Строка %d: не удалось определить секцию или "
                            "ключ" % change.line_number
                        ),
                        severity="warning",
                    )
                )
                continue

            winner = _winner_path(
                change.source if resolved else None,
                source_path,
                forest,
                config_dir,
            )
            if resolved and change.source is None:
                warnings.append(
                    PatchIssue(
                        code="ltx_in_place_no_source",
                        message=(
                            "Строка %d: неизвестен файл-победитель; "
                            "использован открытый файл"
                            % change.line_number
                        ),
                        severity="warning",
                    )
                )
            if winner is None and Path(source_path).is_file():
                winner = source_path
                warnings.append(
                    PatchIssue(
                        code="ltx_in_place_opened_fallback",
                        message=(
                            "Строка %d: владелец недоступен; используется "
                            "открытый файл %s"
                            % (
                                change.line_number,
                                Path(source_path).name,
                            )
                        ),
                        severity="warning",
                    )
                )
            if winner is None:
                warnings.append(
                    PatchIssue(
                        code="ltx_in_place_winner_missing",
                        message=(
                            "Строка %d: не удалось определить файл "
                            "победителя" % change.line_number
                        ),
                        severity="warning",
                    )
                )
                continue

            winner = Path(winner)
            key, value = value_from_new_line(change)
            if key is None:
                warnings.append(
                    PatchIssue(
                        code="ltx_in_place_not_value",
                        message=(
                            "Строка %d: новая строка не является заменой "
                            "значения ключа" % change.line_number
                        ),
                        severity="warning",
                        section=change.section,
                        key=change.key,
                    )
                )
                continue

            source = change.source if resolved else None
            line_number = int(getattr(source, "line", 0) or 0)
            if line_number <= 0:
                line_number = find_winner_line(
                    winner,
                    change.old_line,
                    used=used_lines.get(winner),
                )
            if line_number <= 0:
                line_number = find_section_key_line(
                    winner,
                    change.section,
                    change.key,
                    used=used_lines.get(winner),
                )
            if line_number > 0:
                used_lines.setdefault(winner, set()).add(line_number)
            else:
                warnings.append(
                    PatchIssue(
                        code="ltx_in_place_no_line",
                        message=(
                            "Строка %d: не удалось найти её в файле "
                            "победителя %s"
                            % (change.line_number, winner.name)
                        ),
                        severity="warning",
                    )
                )
                continue

            section = str(change.section)
            grouped.setdefault(winner, {})[(section, key)] = DocumentChange(
                kind="set_key",
                section=section,
                key=key,
                new_value=value,
                risk="safe",
                line_after=line_number,
                note="Строка %d из %s" % (line_number, winner.name),
            )
            notes.append(
                "[%s] %s → %s:%d"
                % (section, key, winner.name, line_number)
            )

        if not grouped:
            fallback = self._fallback_plan(
                source_path,
                baseline_text,
                edited_text,
                line_sources,
                forest,
                options,
                resolved,
            )
            if fallback is None or not fallback.can_generate:
                raise LtxInPlaceError(
                    "In-place не нашёл замен значений, у которых можно "
                    "определить файл-победитель"
                )
            plans = [fallback]
        else:
            plans = self._winner_plans(
                source_path,
                grouped,
                forest,
                options,
                warnings,
            )

        generated_parts: List[GeneratedPatch] = []
        for plan in plans:
            if self.services is not None and hasattr(
                self.services,
                "generate_patch",
            ):
                generated_parts.append(self.services.generate_patch(plan))
            else:
                generated = DltxGenerator().generate(plan)
                generated = replace(
                    generated,
                    verification=VerificationResult(
                        ok=bool(generated.files),
                        checks=(
                            VerificationCheck(
                                name="in_place:%s"
                                % Path(
                                    plan.patch_file_rel or "patch.ltx"
                                ).name,
                                ok=bool(generated.files),
                                detail="DLTX In-place сформирован",
                            ),
                        ),
                    ),
                )
                generated_parts.append(generated)

        files = []
        checks: List[VerificationCheck] = []
        issues: List[PatchIssue] = []
        preview_parts: List[str] = []
        all_ok = True

        for part in generated_parts:
            files.extend(part.files)
            if part.verification is not None:
                checks.extend(part.verification.checks)
                issues.extend(part.verification.issues)
                if not part.verification.ok:
                    all_ok = False
            else:
                all_ok = False
            preview_parts.append("-- Файл: %s" % (
                part.files[0].relative_path if part.files else "?"
            ))
            preview_parts.append(part.text.rstrip())
            preview_parts.append("")

        if not files:
            issues.append(
                PatchIssue(
                    code="ltx_in_place_no_files",
                    message="In-place не создал ни одного патч-файла",
                    severity="error",
                )
            )

        first = plans[0]
        synthetic = PatchPlan(
            source_path=source_path,
            target=first.target,
            patch_mod=first.patch_mod,
            patch_file_rel=first.patch_file_rel,
            changes=tuple(
                change for plan in plans for change in plan.changes
            ),
            issues=tuple(issues),
            warnings=tuple(warnings),
            preview="\n".join(preview_parts).rstrip(),
            diff=None,
            snapshot_id=options.snapshot_id,
            encoding=options.encoding,
            format="ltx_in_place",
            patch_target_dir="configs",
        )

        verification = VerificationResult(
            ok=all_ok and bool(files),
            checks=tuple(checks),
            issues=tuple(issues),
        )
        generated = GeneratedPatch(
            plan=synthetic,
            files=tuple(files),
            text="\n".join(preview_parts).rstrip(),
            verification=verification,
        )
        return LtxInPlaceResult(
            plan=synthetic,
            generated=generated,
            plans=tuple(plans),
            notes=tuple(notes),
        )

    # -------------------------------------------------- планы
    def _winner_plans(
        self,
        source_path: Path,
        grouped,
        forest,
        options: PatchOptions,
        warnings: List[PatchIssue],
    ) -> List[PatchPlan]:
        plans: List[PatchPlan] = []
        for winner in sorted(grouped, key=lambda item: str(item).lower()):
            changes = tuple(grouped[winner].values())
            root, from_patch = _target_root(winner, forest)

            if from_patch:
                warnings.append(
                    PatchIssue(
                        code="ltx_in_place_patch_owner",
                        message=(
                            "Победитель %s — DLTX-патч; патч создаётся "
                            "для его корня %s" % (winner.name, root.name)
                        ),
                        severity="warning",
                    )
                )

            suffix = choose_load_last_suffix(
                root,
                existing_patch_names(root),
                preferred_suffix(options),
            )
            patch_rel = (
                _relative_parent(forest, root)
                / patch_file_name(root, suffix)
            )
            plan = PatchPlan(
                source_path=source_path,
                target=PatchTarget(
                    source_path=source_path,
                    root_path=root,
                    candidates=(root,),
                    reason="DLTX In-place: патч для файла-победителя",
                    ambiguous=False,
                ),
                patch_mod=options.patch_mod or "ACE Patch",
                patch_file_rel=patch_rel,
                changes=changes,
                issues=(),
                warnings=tuple(warnings),
                preview="",
                diff=None,
                snapshot_id=options.snapshot_id,
                encoding=options.encoding,
                format="ltx",
                patch_target_dir="configs",
            )
            plan = replace(plan, preview=DltxGenerator().preview(plan))
            plans.append(plan)
        return plans

    def _fallback_plan(
        self,
        source_path: Path,
        baseline_text: str,
        edited_text: str,
        line_sources,
        forest,
        options: PatchOptions,
        resolved: bool,
    ) -> Optional[PatchPlan]:
        """Структурная правка: обычный DLTX-план вместо замены строк."""
        try:
            from core.patching.planner import PatchPlanner

            planner = PatchPlanner()
            if resolved:
                plan = planner.plan_multiline_edit(
                    source_path,
                    baseline_text,
                    edited_text,
                    line_sources=line_sources,
                    forest=forest,
                    options=options,
                )
            else:
                plan = planner.plan_document_change(
                    source_path,
                    baseline_text,
                    edited_text,
                    forest,
                    options,
                )
        except Exception:
            return None

        if not plan.changes:
            return None

        if not resolved:
            # Правка без резолва должна менять сам открытый файл.
            root, _from_patch = _target_root(source_path, forest)
            suffix = choose_load_last_suffix(
                root,
                existing_patch_names(root),
                preferred_suffix(options),
            )
            plan = replace(
                plan,
                target=PatchTarget(
                    source_path=source_path,
                    root_path=root,
                    candidates=(root,),
                    reason="DLTX In-place: правка открытого файла",
                    ambiguous=False,
                ),
                patch_file_rel=(
                    _relative_parent(forest, root)
                    / patch_file_name(root, suffix)
                ),
            )

        return replace(
            plan,
            format="ltx_in_place",
            preview=DltxGenerator().preview(
                replace(plan, format="ltx_in_place")
            ),
        )
