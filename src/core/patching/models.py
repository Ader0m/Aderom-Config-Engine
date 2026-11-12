# -*- coding: utf-8 -*-
"""Модели данных патч-конвейера.

Эти модели не пишут файлы и не знают о UI. Они описывают:

- намерение изменения;
- цель патча (корень загрузки и файл патч-мода);
- план;
- сгенерированный патч;
- результат проверки.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


class PatchPlanningError(Exception):
    """План нельзя построить."""


# ---------------------------------------------------------------- цели


@dataclass(frozen=True)
class PatchTarget:
    """Куда должен применяться патч."""

    source_path: Path
    root_path: Optional[Path]
    candidates: Tuple[Path, ...] = ()
    reason: str = ""
    ambiguous: bool = False

    @property
    def resolved(self) -> bool:
        return self.root_path is not None and not self.ambiguous


@dataclass(frozen=True)
class PatchOptions:
    """Параметры планирования и генерации."""

    patch_mod: str = "ACE Patch"
    suffix: str = "ace"
    project_tag: str = ""
    allow_delete_section: bool = False
    allow_delete_key: bool = True
    allow_delete_xml_element: bool = True
    target_root: Optional[Path] = None
    snapshot_id: str = ""
    encoding: str = "windows-1251"

    # XML: auto | full_copy | new_file | dxml_lua
    xml_patch_mode: str = "auto"
    xml_new_file_name: str = ""
    xml_new_root_tag: str = ""
    xml_write_full_copy: bool = True


# ---------------------------------------------------------------- проблемы


@dataclass(frozen=True)
class PatchIssue:
    """Проблема плана или проверки."""

    code: str
    message: str
    severity: str = "warning"       # warning | error
    section: Optional[str] = None
    key: Optional[str] = None


# ---------------------------------------------------------------- план


@dataclass(frozen=True)
class PatchPlan:
    """План изменения: что, куда и с какими рисками."""

    source_path: Path
    target: PatchTarget
    patch_mod: str
    patch_file_rel: Optional[Path]
    changes: Tuple[object, ...] = ()
    issues: Tuple[PatchIssue, ...] = ()
    warnings: Tuple[PatchIssue, ...] = ()
    preview: str = ""
    diff: Optional[object] = None
    snapshot_id: str = ""
    encoding: str = "windows-1251"
    confirmed: bool = False

    # XML/DXML: каким способом выражается патч.
    format: str = "ltx"             # ltx | xml | dxml
    xml_mode: str = ""              # full_copy | new_file | dxml_lua
    xml_roots: Tuple[Path, ...] = ()
    xml_root_names: Tuple[str, ...] = ()
    xml_new_file_rel: Optional[Path] = None
    xml_new_root_tag: str = ""
    config_dir: Optional[Path] = None
    patch_target_dir: str = "configs"   # configs | scripts
    after_text: str = ""

    @property
    def is_xml(self) -> bool:
        return self.format in ("xml", "dxml", "xml_fork")

    @property
    def is_dxml(self) -> bool:
        return self.format == "dxml" or self.xml_mode == "dxml_lua"

    @property
    def is_empty(self) -> bool:
        return not self.changes

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)

    @property
    def can_generate(self) -> bool:
        return bool(self.changes) and not self.has_errors

    @property
    def needs_confirmation(self) -> bool:
        return any(change.risk == "confirm" for change in self.changes)

    def errors(self) -> Tuple[PatchIssue, ...]:
        return tuple(
            issue for issue in self.issues if issue.severity == "error"
        )


# ---------------------------------------------------------------- патч


@dataclass(frozen=True)
class PatchFile:
    """Файл, который нужно записать в патч-мод.

    Для текстовых форматов используется ``text``; для двоичных ресурсов
    (например, ``.ogg``) — ``data``. ``data`` имеет приоритет.
    """

    relative_path: Path
    text: str = ""
    encoding: str = "windows-1251"
    data: Optional[bytes] = None


@dataclass(frozen=True)
class VerificationCheck:
    """Одна проверка патча."""

    name: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class VerificationResult:
    """Результат проверки сгенерированного патча."""

    ok: bool
    checks: Tuple[VerificationCheck, ...] = ()
    issues: Tuple[PatchIssue, ...] = ()

    @property
    def failed_checks(self) -> Tuple[VerificationCheck, ...]:
        return tuple(check for check in self.checks if not check.ok)


@dataclass(frozen=True)
class PatchResult:
    """Единый результат планирования для LTX и XML.

    ``LtxPatchService.plan`` и ``XmlPatchService.plan`` возвращают одну и
    ту же форму, даже если конкретный режим создаёт несколько планов.
    """

    plan: PatchPlan
    generated: Optional[GeneratedPatch] = None
    notes: Tuple[str, ...] = ()
    plans: Tuple[PatchPlan, ...] = ()
    keys: Tuple[object, ...] = ()
    references: Tuple[object, ...] = ()


@dataclass(frozen=True)
class GeneratedPatch:
    """Готовый патч, ещё не записанный на диск."""

    plan: PatchPlan
    files: Tuple[PatchFile, ...] = ()
    text: str = ""
    verification: Optional[VerificationResult] = None

    @property
    def ok(self) -> bool:
        if not self.files:
            return False
        if self.verification is None:
            return False
        return self.verification.ok

    @property
    def primary_file(self) -> Optional[PatchFile]:
        return self.files[0] if self.files else None
