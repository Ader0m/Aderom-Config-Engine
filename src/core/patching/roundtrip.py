# -*- coding: utf-8 -*-
"""Полный round-trip LTX-патча через Resolver4.

Проверка не пишет патч в VFS: Resolver4 собирает затронутый корень
дважды — без патча и с виртуально подмешанным патчем. Сравнивается
полный набор секций/ключей, а не только изменения плана.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from core.patching.ltx.document import parse_ltx_document, split_csv
from core.patching.models import (
    GeneratedPatch,
    PatchIssue,
    VerificationCheck,
    VerificationResult,
)


def _canonical_value(value):
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (tuple, list)):
        return tuple(_canonical_value(item) for item in value)
    if isinstance(value, dict):
        return {
            str(key): _canonical_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _canonical_changes(changes):
    return [
        _canonical_value(change)
        for change in (changes or ())
    ]


def generated_fingerprint(generated: GeneratedPatch) -> str:
    """Fingerprint патча, плана и корня, для которого он проверен."""
    digest = hashlib.sha1()

    def add(value) -> None:
        if isinstance(value, (dict, list, tuple)):
            text = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
        else:
            text = str(value)
        digest.update(text.encode("utf-8", "replace"))
        digest.update(b"\0")

    plan = generated.plan
    add(plan.source_path)
    add(plan.patch_mod)
    add(plan.patch_file_rel or "")
    add(plan.snapshot_id)
    add(plan.format)
    add(plan.target.root_path)
    add(plan.target.reason)
    add(plan.xml_mode)
    add(plan.patch_target_dir)
    add(plan.xml_roots)
    add(plan.xml_new_file_rel or "")
    add(plan.config_dir)
    add(plan.asset_mode)
    add(_canonical_changes(plan.changes))
    add(generated.text)
    for file in generated.files:
        add(file.relative_path)
        if file.data is not None:
            digest.update(bytes(file.data))
        else:
            digest.update(file.text.encode(file.encoding, "replace"))
        digest.update(b"\0")
    return digest.hexdigest()


@dataclass(frozen=True)
class RoundTripReport:
    """Доверенный отчёт verifier'а для конкретного патча и снимка."""

    verification: VerificationResult
    plan_id: str
    snapshot_id: str
    _token: object = field(default=None, repr=False, compare=False)

    @property
    def ok(self) -> bool:
        return self.verification.ok

    @property
    def checks(self):
        return self.verification.checks

    @property
    def issues(self):
        return self.verification.issues

    @property
    def failed_checks(self):
        return self.verification.failed_checks


def _norm(value):
    if value is None:
        return None
    if type(value).__name__ == "DLTXToken":
        return None
    return str(value)


def _section_key(value) -> str:
    return str(value or "").lstrip("!").lower()


def _key(value) -> str:
    return str(value or "").lower()


def _sections(section_map):
    return {
        _section_key(name): {
            _key(key): value
            for key, value in values.items()
        }
        for name, values in (section_map or {}).items()
    }


class PatchRoundTripVerifier:
    """Полный diff затронутого LTX-корня после виртуального патча."""

    def __init__(self, resolver_service=None, adapter=None):
        self.resolver_service = resolver_service
        self.adapter = adapter
        self._token = object()

    def verify_report(self, generated: GeneratedPatch) -> RoundTripReport:
        """Проверить и выдать доверенный отчёт с provenance."""
        verification = self.verify(generated)
        return RoundTripReport(
            verification=verification,
            plan_id=generated_fingerprint(generated),
            snapshot_id=str(generated.plan.snapshot_id or ""),
            _token=self._token,
        )

    def _get_adapter(self):
        if self.adapter is not None:
            return self.adapter
        if self.resolver_service is None:
            return None
        return self.resolver_service.get_adapter()

    def verify(self, generated: GeneratedPatch) -> VerificationResult:
        plan = generated.plan
        if plan.is_xml:
            # XML уже проверяется XmlResolverPatchVerifier на полном
            # resolved-документе; здесь второго источника истины нет.
            return VerificationResult(
                ok=True,
                checks=(
                    VerificationCheck(
                        name="roundtrip",
                        ok=True,
                        detail="XML проверен XmlResolverPatchVerifier",
                    ),
                ),
            )

        if plan.format == "asset" or plan.target.root_path is None:
            return VerificationResult(
                ok=True,
                checks=(
                    VerificationCheck(
                        name="roundtrip",
                        ok=True,
                        detail=(
                            "Полный round-trip не применим: "
                            "нет корня загрузки"
                        ),
                    ),
                ),
            )

        if not generated.files:
            return VerificationResult(
                ok=False,
                checks=(
                    VerificationCheck(
                        name="roundtrip",
                        ok=False,
                        detail="Нет файлов патча",
                    ),
                ),
                issues=(
                    PatchIssue(
                        code="empty_patch",
                        message="Сгенерированный патч пуст",
                        severity="error",
                    ),
                ),
            )

        adapter = self._get_adapter()
        if adapter is None:
            return VerificationResult(
                ok=False,
                checks=(
                    VerificationCheck(
                        name="roundtrip",
                        ok=False,
                        detail="Нет адаптера Resolver4",
                    ),
                ),
                issues=(
                    PatchIssue(
                        code="roundtrip_unavailable",
                        message="Нет адаптера Resolver4 для полного diff",
                        severity="error",
                    ),
                ),
            )

        root = Path(plan.target.root_path)
        patch_filename = Path(generated.files[0].relative_path).name
        try:
            baseline, baseline_parents = adapter.resolve_sections_details(
                root,
            )
            patched, sources, duplicates, patched_parents = (
                adapter.resolve_with_virtual_patch_details(
                    root,
                    patch_filename,
                    generated.text,
                )
            )
        except Exception as exc:
            return VerificationResult(
                ok=False,
                checks=(
                    VerificationCheck(
                        name="roundtrip",
                        ok=False,
                        detail=str(exc),
                    ),
                ),
                issues=(
                    PatchIssue(
                        code="roundtrip_failed",
                        message="Round-trip Resolver4 не выполнен: %s" % exc,
                        severity="error",
                    ),
                ),
            )

        return self._compare(
            generated,
            baseline,
            baseline_parents,
            patched,
            patched_parents,
            sources,
            duplicates,
            patch_filename,
        )

    def _compare(
        self,
        generated: GeneratedPatch,
        baseline,
        baseline_parents,
        patched,
        patched_parents,
        sources,
        duplicates,
        patch_filename: str,
    ) -> VerificationResult:
        plan = generated.plan
        checks: List[VerificationCheck] = []
        issues: List[PatchIssue] = []

        base = _sections(baseline)
        after = _sections(patched)
        parents = {
            _section_key(name): tuple(
                _section_key(parent)
                for parent in values
            )
            for name, values in (baseline_parents or {}).items()
        }
        after_parents = {
            _section_key(name): tuple(
                _section_key(parent)
                for parent in values
            )
            for name, values in (patched_parents or {}).items()
        }
        source_map = {
            _section_key(name): {
                _key(key): value
                for key, value in values.items()
            }
            for name, values in (sources or {}).items()
        }

        expected_deleted_sections = {
            _section_key(change.section)
            for change in plan.changes
            if change.kind == "delete_section" and change.section
        }
        expected_added_sections = {
            _section_key(change.section)
            for change in plan.changes
            if change.kind == "add_section" and change.section
        }
        expected_deleted_keys = {
            (_section_key(change.section), _key(change.key))
            for change in plan.changes
            if change.kind == "delete_key"
            and change.section
            and change.key
        }
        allowed_added_keys = {
            (_section_key(change.section), _key(change.key))
            for change in plan.changes
            if change.kind in ("add_key", "set_key")
            and change.section
            and change.key
        }
        expected_changed_keys = {
            (_section_key(change.section), _key(change.key))
            for change in plan.changes
            if change.kind in (
                "set_key",
                "add_key",
                "list_append",
                "list_remove",
            )
            and change.section
            and change.key
        }
        removed_parents = {
            (_section_key(change.section), _section_key(change.parent))
            for change in plan.changes
            if change.kind == "remove_parent"
            and change.section
            and change.parent
        }
        parent_changes = {}
        for change in plan.changes:
            if change.kind not in ("add_parent", "remove_parent"):
                continue
            section = _section_key(change.section)
            parent = _section_key(change.parent)
            if section and parent:
                parent_changes.setdefault(section, []).append(
                    (change.kind, parent)
                )
        expected_parents = {}
        for section, changes in parent_changes.items():
            current = list(parents.get(section, ()))
            for kind, parent in changes:
                if kind == "remove_parent":
                    current = [item for item in current if item != parent]
                elif parent not in current:
                    current.append(parent)
            expected_parents[section] = tuple(dict.fromkeys(current))

        def _parent_edges(section):
            """Все рёбра section -> ... -> предок в baseline-цепочке."""
            edges = []
            seen = set()
            stack = [
                (section, parent)
                for parent in parents.get(section, ())
            ]
            while stack:
                child, parent = stack.pop()
                if not child or not parent or (child, parent) in seen:
                    continue
                seen.add((child, parent))
                edges.append((child, parent))
                stack.extend(
                    (parent, grand)
                    for grand in parents.get(parent, ())
                )
            return edges

        def _explained_by_parent(section, key):
            for child, parent in _parent_edges(section):
                if key not in base.get(parent, {}):
                    continue
                if (child, parent) in removed_parents:
                    return True
                if parent in expected_deleted_sections:
                    return True
                if (parent, key) in expected_deleted_keys:
                    return True
            return False

        base_sections = set(base)
        after_sections = set(after)
        removed_sections = base_sections - after_sections
        added_sections = after_sections - base_sections

        for name in sorted(removed_sections - expected_deleted_sections):
            issues.append(
                PatchIssue(
                    code="roundtrip_section_lost",
                    message="После патча потеряна секция [%s]" % name,
                    severity="error",
                    section=name,
                )
            )
        for name in sorted(expected_deleted_sections & after_sections):
            issues.append(
                PatchIssue(
                    code="roundtrip_section_not_deleted",
                    message="Секция [%s] не удалена патчем" % name,
                    severity="error",
                    section=name,
                )
            )
        for name in sorted(added_sections - expected_added_sections):
            issues.append(
                PatchIssue(
                    code="roundtrip_section_added",
                    message="Появилась незапланированная секция [%s]" % name,
                    severity="warning",
                    section=name,
                )
            )

        common_sections = sorted(base_sections & after_sections)
        removed_keys = 0
        added_keys = 0
        changed_values = 0
        for name in common_sections:
            base_values = base[name]
            after_values = after[name]
            base_keys = set(base_values)
            after_keys = set(after_values)
            removed = base_keys - after_keys
            added = after_keys - base_keys
            removed_keys += len(removed)
            added_keys += len(added)

            for key in sorted(removed):
                if (name, key) in expected_deleted_keys:
                    continue
                if _explained_by_parent(name, key):
                    continue
                issues.append(
                    PatchIssue(
                        code="roundtrip_key_lost",
                        message=(
                            "После патча потерян ключ [%s] %s"
                            % (name, key)
                        ),
                        severity="error",
                        section=name,
                        key=key,
                    )
                )
            for key in sorted(added):
                if (name, key) in allowed_added_keys:
                    continue
                issues.append(
                    PatchIssue(
                        code="roundtrip_key_added",
                        message=(
                            "Появился незапланированный ключ [%s] %s"
                            % (name, key)
                        ),
                        severity="warning",
                        section=name,
                        key=key,
                    )
                )
            for key in sorted(base_keys & after_keys):
                if _norm(base_values[key]) == _norm(after_values[key]):
                    continue
                changed_values += 1
                if (name, key) not in expected_changed_keys:
                    issues.append(
                        PatchIssue(
                            code="roundtrip_value_changed",
                            message=(
                                "Значение [%s] %s изменилось вне плана"
                                % (name, key)
                            ),
                            severity="warning",
                            section=name,
                            key=key,
                        )
                    )

        for change in plan.changes:
            name = _section_key(change.section)
            key = _key(change.key)
            if change.kind in ("set_key", "add_key"):
                actual = after.get(name, {}).get(key)
                if _norm(actual) != _norm(change.new_value):
                    issues.append(
                        PatchIssue(
                            code="roundtrip_value_mismatch",
                            message=(
                                "Ожидалось [%s] %s = %r, получено %r"
                                % (
                                    name,
                                    key,
                                    change.new_value,
                                    actual,
                                )
                            ),
                            severity="error",
                            section=name,
                            key=key,
                        )
                    )
            elif change.kind == "delete_key":
                if key in after.get(name, {}):
                    issues.append(
                        PatchIssue(
                            code="roundtrip_delete_mismatch",
                            message=(
                                "Ключ [%s] %s не удалён" % (name, key)
                            ),
                            severity="error",
                            section=name,
                            key=key,
                        )
                    )
            elif change.kind in ("list_append", "list_remove"):
                actual = _norm(after.get(name, {}).get(key))
                items = split_csv(actual or "")
                item = _norm(change.new_value) or ""
                ok = (
                    item in items
                    if change.kind == "list_append"
                    else item not in items
                )
                if not ok:
                    issues.append(
                        PatchIssue(
                            code="roundtrip_list_mismatch",
                            message=(
                                "Операция %s для [%s] %s не дала "
                                "ожидаемого списка"
                                % (change.kind, name, key)
                            ),
                            severity="error",
                            section=name,
                            key=key,
                        )
                    )
            elif change.kind == "delete_section":
                if name in after:
                    issues.append(
                        PatchIssue(
                            code="roundtrip_section_delete_mismatch",
                            message="Секция [%s] не удалена" % name,
                            severity="error",
                            section=name,
                        )
                    )
            elif change.kind == "add_section":
                if name not in after:
                    issues.append(
                        PatchIssue(
                            code="roundtrip_section_add_mismatch",
                            message="Секция [%s] не создана" % name,
                            severity="error",
                            section=name,
                        )
                    )

        parent_errors = 0
        for section in sorted(set(parents) | set(after_parents)):
            if section in expected_deleted_sections:
                continue
            if section in added_sections:
                continue
            before = set(parents.get(section, ()))
            actual = set(after_parents.get(section, ()))
            if section in expected_parents:
                expected = set(expected_parents[section])
                if actual != expected:
                    parent_errors += 1
                    issues.append(
                        PatchIssue(
                            code="roundtrip_parents_mismatch",
                            message=(
                                "Наследование [%s] не совпало: "
                                "ожидалось %s, получено %s"
                                % (
                                    section,
                                    sorted(expected),
                                    sorted(actual),
                                )
                            ),
                            severity="error",
                            section=section,
                        )
                    )
            elif before != actual:
                issues.append(
                    PatchIssue(
                        code="roundtrip_parents_changed",
                        message=(
                            "Наследование [%s] изменилось вне плана: "
                            "%s -> %s"
                            % (
                                section,
                                sorted(before),
                                sorted(actual),
                            )
                        ),
                        severity="warning",
                        section=section,
                    )
                )

        for name, keys in sorted((duplicates or {}).items()):
            issues.append(
                PatchIssue(
                    code="roundtrip_duplicate_key",
                    message=(
                        "В [%s] остались дубли ключей: %s"
                        % (name, ", ".join(str(key) for key in keys))
                    ),
                    severity="error",
                    section=name,
                )
            )

        artifact = []
        for name, values in after.items():
            for key, value in values.items():
                if type(value).__name__ == "DLTXToken":
                    artifact.append("[%s] %s = %s" % (name, key, value))
                elif isinstance(value, str) and (
                    "DLTX_DELETE" in value or "<DELETE>" in value
                ):
                    artifact.append("[%s] %s" % (name, key))
        if artifact:
            issues.append(
                PatchIssue(
                    code="roundtrip_dltx_artifact",
                    message=(
                        "В итоговом корне остались DLTX-артефакты: %s"
                        % "; ".join(artifact[:20])
                    ),
                    severity="error",
                )
            )

        expected_stem = Path(patch_filename).stem.lower()
        source_errors = []
        for change in plan.changes:
            if change.kind not in ("set_key", "add_key", "list_append", "list_remove"):
                continue
            name = _section_key(change.section)
            key = _key(change.key)
            source = source_map.get(name, {}).get(key)
            if not source:
                source_errors.append("[%s] %s: источник не найден" % (name, key))
                continue
            if Path(str(source)).stem.lower() != expected_stem:
                source_errors.append(
                    "[%s] %s: %s" % (name, key, source)
                )
        if source_errors:
            issues.append(
                PatchIssue(
                    code="roundtrip_source_mismatch",
                    message=(
                        "Итоговое значение пришло не из патча: %s"
                        % "; ".join(source_errors[:20])
                    ),
                    severity="error",
                )
            )

        patch_doc = parse_ltx_document(
            generated.text,
            source_path=plan.source_path,
        )
        for name, section in patch_doc.sections.items():
            seen = set()
            repeated = []
            for entry in section.entries:
                if entry.operation != "set":
                    continue
                key = _key(entry.key)
                if key in seen:
                    repeated.append(entry.key)
                seen.add(key)
            if repeated:
                issues.append(
                    PatchIssue(
                        code="roundtrip_duplicate_patch_key",
                        message=(
                            "Патч дублирует ключи [%s]: %s"
                            % (name, ", ".join(repeated))
                        ),
                        severity="error",
                        section=name,
                    )
                )

        header_counts = {}
        for name, _line in patch_doc.headers:
            key = _section_key(name)
            header_counts[key] = header_counts.get(key, 0) + 1
        duplicate_headers = [
            name for name, count in header_counts.items() if count > 1
        ]
        if duplicate_headers:
            issues.append(
                PatchIssue(
                    code="roundtrip_duplicate_patch_section",
                    message=(
                        "Патч повторно объявляет секции: %s"
                        % ", ".join(sorted(duplicate_headers))
                    ),
                    severity="error",
                )
            )

        errors = [issue for issue in issues if issue.severity == "error"]
        checks.append(
            VerificationCheck(
                name="parents",
                ok=parent_errors == 0,
                detail=(
                    "Наследование совпадает с планом"
                    if parent_errors == 0
                    else "Наследование не совпало в %d секциях"
                    % parent_errors
                ),
            )
        )
        checks.append(
            VerificationCheck(
                name="full_diff",
                ok=not errors,
                detail=(
                    "Секций до/после: %d/%d; удалено секций: %d; "
                    "добавлено секций: %d; ключей удалено: %d; "
                    "ключей добавлено: %d; значений изменено: %d"
                    % (
                        len(base_sections),
                        len(after_sections),
                        len(removed_sections),
                        len(added_sections),
                        removed_keys,
                        added_keys,
                        changed_values,
                    )
                ),
            )
        )
        checks.append(
            VerificationCheck(
                name="source",
                ok=not source_errors,
                detail=(
                    "Итоговые значения принадлежат патчу"
                    if not source_errors
                    else "; ".join(source_errors[:20])
                ),
            )
        )
        checks.append(
            VerificationCheck(
                name="dltx_artifacts",
                ok=not artifact,
                detail=(
                    "DLTX-артефактов нет"
                    if not artifact
                    else "; ".join(artifact[:20])
                ),
            )
        )

        ok = all(check.ok for check in checks) and not errors
        return VerificationResult(
            ok=ok,
            checks=tuple(checks),
            issues=tuple(issues),
        )


__all__ = [
    "PatchRoundTripVerifier",
    "RoundTripReport",
    "generated_fingerprint",
]
