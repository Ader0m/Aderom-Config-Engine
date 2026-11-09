# -*- coding: utf-8 -*-
"""DLTX Override: полная копия LTX-файла-победителя строки.

Режим не формирует DLTX-дельту. Он берёт физический LTX-файл, в котором
находится изменённая строка (победитель), и записывает его полную копию с
заменённой строкой. Победителем может быть корневой файл, include-файл или
чужой DLTX-патч: в опасном режиме Override его файл тоже перезаписывается.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from ..documents.document_diff import DocumentChange
from ..documents.document_model import parse_key_line, parse_section_header
from ..documents.resolved_edit import (
    ResolvedLineChange,
    analyze_resolved_changes,
    line_context,
)
from ..fs import strip_comments
from .patch_models import (
    GeneratedPatch,
    PatchFile,
    PatchIssue,
    PatchOptions,
    PatchPlan,
    PatchTarget,
    VerificationCheck,
    VerificationResult,
)
from .patch_targets import sanitize_name, xml_relative_path


class LtxOverrideError(Exception):
    """LTX Override нельзя построить."""


@dataclass(frozen=True)
class LtxOverrideResult:
    plan: PatchPlan
    generated: GeneratedPatch


def _resolve_source_path(
    source,
    source_path: Path,
    forest=None,
    config_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Найти физический путь победителя, даже если LineSource.path пуст."""
    path = getattr(source, "path", None)
    if path is not None:
        candidate = Path(path)
        if candidate.is_file():
            return candidate

    display = str(getattr(source, "display", "") or "").strip()
    if not display:
        return None

    normalized = display.replace("/", "\\")
    if config_dir is None:
        config_dir = (
            Path(getattr(forest, "scan_dir"))
            if forest is not None and getattr(forest, "scan_dir", None)
            else source_path.parent
        )

    upper = normalized.upper()
    if upper.startswith("GAME\\"):
        candidate = Path(config_dir) / normalized[5:]
        if candidate.is_file():
            return candidate

    if upper.startswith("MO2\\"):
        rest = normalized[4:]
        parts = rest.split("\\", 1)
        if len(parts) == 2:
            mod_name, rel = parts
            for mods_root in _mods_roots(config_dir):
                candidate = mods_root / mod_name / rel
                if candidate.is_file():
                    return candidate

    candidate = Path(display)
    if candidate.is_file():
        return candidate

    return None


def _mods_roots(config_dir: Path) -> List[Path]:
    bases = [Path(config_dir)]
    try:
        bases.extend(list(Path(config_dir).parents)[:6])
    except Exception:
        pass
    roots: List[Path] = []
    seen = set()
    for base in bases:
        if base.name.lower() == "mods" and base.is_dir():
            candidates = [base]
        else:
            candidates = [
                base / "mods",
                base / "MO2" / "mods",
                base / "ModOrganizer" / "mods",
                base / "ModOrganizer2" / "mods",
            ]
        for candidate in candidates:
            if not candidate.is_dir():
                continue
            try:
                key = str(candidate.resolve()).lower()
            except OSError:
                key = str(candidate).lower()
            if key in seen:
                continue
            seen.add(key)
            roots.append(candidate)
    return roots


def _winner_relative_path(
    source,
    winner: Path,
    forest=None,
    config_dir: Optional[Path] = None,
) -> Path:
    """Путь файла-победителя относительно ``gamedata/configs``."""
    display = str(getattr(source, "display", "") or "").strip()
    normalized = display.replace("/", "\\")
    upper = normalized.upper()

    if upper.startswith("GAME\\"):
        return Path(normalized[5:])
    if upper.startswith("MO2\\"):
        rest = normalized[4:]
        parts = rest.split("\\", 1)
        if len(parts) == 2:
            rel = parts[1]
            marker = "gamedata\\configs\\"
            low = rel.lower()
            index = low.find(marker)
            if index >= 0:
                return Path(rel[index + len(marker):])
            return Path(rel)

    if ":" in display and "\\gamedata\\configs\\" in normalized.lower():
        low = normalized.lower()
        marker = "\\gamedata\\configs\\"
        index = low.find(marker)
        return Path(normalized[index + len(marker):])

    relative = xml_relative_path(forest, winner)
    if relative.name == winner.name and len(relative.parts) == 1:
        # xml_relative_path не нашёл файл в дереве; пробуем физический путь.
        if config_dir is not None:
            try:
                return Path(winner).relative_to(
                    Path(config_dir).parent / "configs"
                )
            except ValueError:
                pass
    return relative


def _read_ltx(path: Path) -> Tuple[str, str]:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace"), "utf-8-sig"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("windows-1251", errors="replace"), "windows-1251"


def _line_ending(line: str) -> Tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    if line.endswith("\r"):
        return line[:-1], "\r"
    return line, ""


def _find_winner_line(
    path: Path,
    old_line: str,
    used: Optional[set] = None,
) -> int:
    """Найти номер строки в файле-победителе по её тексту."""
    text, _encoding = _read_ltx(path)
    needle = str(old_line or "").strip()
    if not needle:
        return 0
    used = used or set()
    for number, line in enumerate(text.splitlines(), 1):
        if number in used:
            continue
        if line.strip() == needle:
            return number
    return 0


def _find_section_key_line(
    path: Path,
    section: Optional[str],
    key: Optional[str],
    used: Optional[set] = None,
) -> int:
    """Найти строку ключа по секции и имени.

    Нужен, когда resolved-строка не совпадает с физической строкой
    победителя (например, из-за уже применённого DLTX-патча).
    """
    if not section or not key:
        return 0

    text, _encoding = _read_ltx(path)
    used = used or set()
    current = None
    wanted_section = str(section).lower()
    wanted_key = str(key).lower()

    for number, line in enumerate(text.splitlines(), 1):
        clean = strip_comments(line).strip()
        if not clean:
            continue

        header = parse_section_header(clean)
        if header is not None:
            current = header[1]
            continue

        parsed = parse_key_line(clean)
        if parsed is None:
            continue

        operation, found_key, _raw, has_value = parsed
        if (
            operation == "set"
            and has_value
            and current == wanted_section
            and found_key.lower() == wanted_key
            and number not in used
        ):
            return number

    return 0


def _apply_replacements(
    path: Path,
    replacements: Dict[int, str],
) -> Tuple[str, str]:
    text, encoding = _read_ltx(path)
    lines = text.splitlines(keepends=True)

    for line_number, new_text in sorted(
        replacements.items(),
        reverse=True,
    ):
        index = int(line_number) - 1
        if index < 0 or index >= len(lines):
            continue
        _old, ending = _line_ending(lines[index])
        lines[index] = str(new_text) + ending

    return "".join(lines), encoding


class LtxOverrideService:
    """Планирование полной копии LTX-победителя."""

    def plan(
        self,
        source_path,
        baseline_text: str,
        edited_text: str,
        line_sources=(),
        forest=None,
        options: Optional[PatchOptions] = None,
        resolved: bool = False,
    ) -> LtxOverrideResult:
        source_path = Path(source_path)
        options = options or PatchOptions()
        config_dir = (
            Path(getattr(forest, "scan_dir"))
            if forest is not None and getattr(forest, "scan_dir", None)
            else source_path.parent
        )

        if source_path.suffix.lower() != ".ltx":
            raise LtxOverrideError(
                "DLTX Override поддерживает только .ltx: %s" % source_path
            )

        warnings: List[PatchIssue] = []
        changes: List[DocumentChange] = []
        replacements: Dict[Path, Dict[int, str]] = {}
        used_lines: Dict[Path, set] = {}
        source_by_winner: Dict[Path, object] = {}

        if not resolved:
            # Сырой файл: все строки — оригинальные, копируем файл целиком.
            replacements[source_path] = {}
            changes.append(
                DocumentChange(
                    kind="override_file",
                    section="",
                    risk="confirm",
                    note="Полная копия изменённого LTX-файла",
                )
            )
        else:
            old_lines = str(baseline_text or "").replace(
                "\r\n", "\n"
            ).replace("\r", "\n").split("\n")
            new_lines = str(edited_text or "").replace(
                "\r\n", "\n"
            ).replace("\r", "\n").split("\n")

            if len(old_lines) == len(new_lines):
                # Для system.ltx в миллион строк полный SequenceMatcher
                # недопустимо дорог. При равном числе строк это просто
                # построчная замена.
                resolved_changes = []
                sources = list(line_sources or ())
                sections, keys, _section_lines, _key_lines = line_context(
                    old_lines
                )
                for index, (old_line, new_line) in enumerate(
                    zip(old_lines, new_lines)
                ):
                    if old_line == new_line:
                        continue
                    resolved_changes.append(
                        ResolvedLineChange(
                            line_number=index + 1,
                            old_line=old_line,
                            new_line=new_line,
                            section=(
                                sections[index]
                                if index < len(sections)
                                else None
                            ),
                            key=(
                                keys[index] if index < len(keys) else None
                            ),
                            source=(
                                sources[index]
                                if index < len(sources)
                                else None
                            ),
                            kind="value",
                        )
                    )
            else:
                resolved_changes = list(
                    analyze_resolved_changes(
                        baseline_text,
                        edited_text,
                        line_sources,
                    ).changes
                )

            for change in resolved_changes:
                if change.kind != "value":
                    warnings.append(
                        PatchIssue(
                            code="ltx_override_structural",
                            message=(
                                "Строка %d: структурная правка не "
                                "поддержана Override" % change.line_number
                            ),
                            severity="warning",
                        )
                    )
                    continue

                source = change.source
                if source is None:
                    warnings.append(
                        PatchIssue(
                            code="ltx_override_no_source",
                            message=(
                                "Строка %d: неизвестен файл-победитель; "
                                "используется открытый файл"
                                % change.line_number
                            ),
                            severity="warning",
                        )
                    )

                source_kind = getattr(source, "kind", None)
                if source_kind == "patch":
                    warnings.append(
                        PatchIssue(
                            code="ltx_override_dltx_patch_winner",
                            message=(
                                "Строка %d: победитель — DLTX-патч '%s'. "
                                "Опасный режим Override перезапишет этот "
                                "патч своим файлом."
                                % (
                                    change.line_number,
                                    getattr(source, "display", "")
                                    or getattr(source, "path", "?"),
                                )
                            ),
                            severity="warning",
                        )
                    )

                if source is None or source_kind == "original":
                    # Строка помечена как «оригинал» или владелец
                    # неизвестен: меняется сам открытый файл.
                    winner = source_path
                else:
                    winner = _resolve_source_path(
                        source,
                        source_path,
                        forest=forest,
                        config_dir=config_dir,
                    )
                if winner is None:
                    fallback = getattr(source, "path", None)
                    if fallback is not None and Path(fallback).is_file():
                        winner = Path(fallback)
                if winner is None and Path(source_path).is_file():
                    winner = source_path
                    warnings.append(
                        PatchIssue(
                            code="ltx_override_opened_fallback",
                            message=(
                                "Строка %d: владелец недоступен; "
                                "используется открытый файл %s"
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
                            code="ltx_override_winner_missing",
                            message=(
                                "Строка %d: не удалось определить файл "
                                "победителя '%s'"
                                % (
                                    change.line_number,
                                    getattr(source, "display", "")
                                    or getattr(source, "path", "?"),
                                )
                            ),
                            severity="warning",
                        )
                    )
                    continue
                winner = Path(winner)
                line_number = int(getattr(source, "line", 0) or 0)
                if line_number <= 0:
                    line_number = _find_winner_line(
                        winner,
                        change.old_line,
                        used=used_lines.get(winner),
                    )
                if line_number <= 0:
                    # Resolved-строка могла быть изменена DLTX-патчем,
                    # поэтому ищем ключ по секции, а не по тексту строки.
                    line_number = _find_section_key_line(
                        winner,
                        change.section,
                        change.key,
                        used=used_lines.get(winner),
                    )
                if line_number > 0:
                    used_lines.setdefault(winner, set()).add(line_number)
                if line_number <= 0:
                    # Победитель не содержит эту строку: используем
                    # фактический файл, если он отличается от корня.
                    fallback = getattr(source, "path", None)
                    if fallback is not None:
                        fallback = Path(fallback)
                        if fallback != winner and fallback.is_file():
                            alt_line = _find_winner_line(
                                fallback,
                                change.old_line,
                                used=used_lines.get(fallback),
                            )
                            if alt_line <= 0:
                                alt_line = _find_section_key_line(
                                    fallback,
                                    change.section,
                                    change.key,
                                    used=used_lines.get(fallback),
                                )
                            if alt_line > 0:
                                warnings.append(
                                    PatchIssue(
                                        code="ltx_override_root_fallback",
                                        message=(
                                            "Строка %d: корень %s не содержит "
                                            "строку; используется фактический "
                                            "файл %s"
                                            % (
                                                change.line_number,
                                                winner.name,
                                                fallback.name,
                                            )
                                        ),
                                        severity="warning",
                                    )
                                )
                                winner = fallback
                                line_number = alt_line
                                used_lines.setdefault(
                                    winner, set()
                                ).add(line_number)
                if line_number <= 0:
                    warnings.append(
                        PatchIssue(
                            code="ltx_override_no_line",
                            message=(
                                "Строка %d: не удалось найти её в файле-"
                                "победителе %s"
                                % (change.line_number, winner.name)
                            ),
                            severity="warning",
                        )
                    )
                    continue

                replacements.setdefault(winner, {})[line_number] = change.new_line
                source_by_winner[winner] = source
                changes.append(
                    DocumentChange(
                        kind="override_line",
                        section=change.section or "",
                        key=change.key,
                        new_value=change.new_line,
                        risk="confirm",
                        line_after=line_number,
                        note="Строка %d из %s" % (
                            line_number,
                            winner.name,
                        ),
                    )
                )

        if not replacements:
            raise LtxOverrideError(
                "Нет изменений строк, у которых победитель — LTX-файл "
                "или DLTX-патч"
            )

        files: List[PatchFile] = []
        checks: List[VerificationCheck] = []
        issues: List[PatchIssue] = list(warnings)

        for winner, line_map in replacements.items():
            if not winner.is_file():
                issues.append(
                    PatchIssue(
                        code="ltx_override_winner_missing",
                        message="Файл-победитель не найден: %s" % winner,
                        severity="error",
                    )
                )
                continue

            if line_map:
                text, encoding = _apply_replacements(winner, line_map)
            else:
                _original, encoding = _read_ltx(winner)
                text = edited_text

            rel = _winner_relative_path(
                source_by_winner.get(winner),
                winner,
                forest=forest,
                config_dir=config_dir,
            )
            if not str(rel):
                issues.append(
                    PatchIssue(
                        code="ltx_override_path",
                        message="Не удалось определить путь %s" % winner,
                        severity="error",
                    )
                )
                continue

            files.append(
                PatchFile(
                    relative_path=Path("gamedata") / "configs" / rel,
                    text=text,
                    encoding=encoding,
                )
            )

            # Проверка: повторяем замену из оригинала и сравниваем.
            if line_map:
                expected, _encoding = _apply_replacements(winner, line_map)
                ok = expected == text
            else:
                ok = True
            checks.append(
                VerificationCheck(
                    name="override:%s" % winner.name,
                    ok=ok,
                    detail=(
                        "Полная копия совпадает с ожидаемой"
                        if ok
                        else "Полная копия не совпала с ожидаемой"
                    ),
                )
            )
            if not ok:
                issues.append(
                    PatchIssue(
                        code="ltx_override_mismatch",
                        message="Полная копия %s не совпала" % winner.name,
                        severity="error",
                    )
                )

        if not files:
            issues.append(
                PatchIssue(
                    code="ltx_override_no_files",
                    message="Override не создал файлов",
                    severity="error",
                )
            )

        target = PatchTarget(
            source_path=source_path,
            root_path=source_path,
            candidates=(source_path,),
            reason="DLTX Override: копия файла-победителя",
            ambiguous=False,
        )
        plan = PatchPlan(
            source_path=source_path,
            target=target,
            patch_mod=options.patch_mod or "ACE Patch",
            patch_file_rel=(
                Path(*files[0].relative_path.parts[2:])
                if files else None
            ),
            changes=tuple(changes),
            issues=tuple(issues),
            warnings=tuple(warnings),
            preview="",
            diff=None,
            snapshot_id=options.snapshot_id,
            encoding=files[0].encoding if files else "windows-1251",
            format="ltx_override",
            patch_target_dir="configs",
        )

        verification = VerificationResult(
            ok=not any(issue.severity == "error" for issue in issues)
            and bool(files),
            checks=tuple(checks)
            + (
                VerificationCheck(
                    name="resolver",
                    ok=True,
                    detail=(
                        "DLTX Override: полная копия файла не требует "
                        "виртуального DLTX-патча"
                    ),
                ),
            ),
            issues=tuple(issues),
        )
        preview_parts = []
        for file in files:
            preview_parts.append("; === %s ===" % file.relative_path)
            preview_parts.append(file.text.rstrip())
            preview_parts.append("")
        preview = "\n".join(preview_parts)
        generated = GeneratedPatch(
            plan=plan,
            files=tuple(files),
            text=preview,
            verification=verification,
        )
        return LtxOverrideResult(plan=plan, generated=generated)
