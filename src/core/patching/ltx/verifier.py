# -*- coding: utf-8 -*-
"""Проверка сгенерированного патча.

Сейчас это проверка на уровне модели документа: патч применяется к модели
оригинала, после чего результат сравнивается с моделью черновика. Это
отлавливает ошибки генерации, потерю ключей, неверные секции и опасные
пути.

Полная проверка через внешний резолвер (round-trip в терминах движка)
остаётся следующей ступенью: для неё нужно виртуально подмешать патч в
загрузку ``Resolver4``, не записывая его в VFS.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

from core.patching.ltx.document import (
    LtxDocument,
    LtxSectionModel,
    parse_ltx_document,
    split_csv,
)
from core.patching.models import (
    GeneratedPatch,
    PatchIssue,
    VerificationCheck,
    VerificationResult,
)
from core.patching.verifier import BasePatchVerifier


def _apply_section(
    result: LtxDocument,
    patch_section: LtxSectionModel,
) -> Tuple[bool, str]:
    existing = result.sections.get(patch_section.name)

    if patch_section.header_kind == "base":
        if existing is not None:
            return (
                False,
                "Патч объявляет [%s] как базовую секцию, но она уже есть"
                % patch_section.name,
            )
        result.sections[patch_section.name] = LtxSectionModel(
            name=patch_section.name,
            header_kind="base",
            parents=list(patch_section.parents),
            entries=list(patch_section.entries),
            line_number=patch_section.line_number,
        )
        return True, ""

    if existing is None:
        if patch_section.header_kind == "override":
            return (
                False,
                "Патч переопределяет несуществующую секцию [%s]"
                % patch_section.name,
            )

        result.sections[patch_section.name] = LtxSectionModel(
            name=patch_section.name,
            header_kind=patch_section.header_kind,
            parents=list(patch_section.parents),
            entries=list(patch_section.entries),
            line_number=patch_section.line_number,
        )
        return True, ""

    existing.parents.extend(patch_section.parents)
    existing.entries.extend(patch_section.entries)
    return True, ""


def _mutable_section(result: LtxDocument, name: str) -> None:
    """Скопировать секцию перед изменением, не трогая остальной документ.

    ``copy.deepcopy`` всего документа на resolved-файле в десятки мегабайт
    копировал миллионы объектов и занимал минуты. Патч меняет только
    перечисленные в нём секции, поэтому остальные можно разделять с
    исходной моделью.
    """
    existing = result.sections.get(name)
    if existing is None:
        return

    result.sections[name] = LtxSectionModel(
        name=existing.name,
        header_kind=existing.header_kind,
        parents=list(existing.parents),
        entries=list(existing.entries),
        line_number=existing.line_number,
    )


def apply_patch_document(
    base: LtxDocument,
    patch: LtxDocument,
) -> Tuple[LtxDocument, List[PatchIssue]]:
    """Применить модель патча к модели оригинала."""
    result = LtxDocument(
        source_path=base.source_path,
        text=base.text,
        sections=dict(base.sections),
        deleted_sections=set(base.deleted_sections),
        includes=base.includes,
        diagnostics=base.diagnostics,
    )
    issues: List[PatchIssue] = []

    for name in sorted(patch.deleted_sections):
        result.deleted_sections.add(name)
        result.sections.pop(name, None)

    for name in sorted(patch.sections):
        _mutable_section(result, name)
        ok, message = _apply_section(result, patch.sections[name])
        if not ok:
            issues.append(
                PatchIssue(
                    code="patch_apply",
                    message=message,
                    severity="error",
                    section=name,
                )
            )

    return result, issues


def _compare_documents(
    result: LtxDocument,
    desired: LtxDocument,
    changed_sections=None,
) -> Tuple[List[VerificationCheck], List[PatchIssue]]:
    """Сравнить применённый патч с желаемым документом.

    ``changed_sections`` ограничивает детальное сравнение секциями, которые
    реально фигурируют в плане. Для остальных секций diff уже доказал
    отсутствие изменений, а патч их не трогает: строить для них ``view()``
    на resolved-документе в десятки мегабайт бессмысленно.
    """
    checks: List[VerificationCheck] = []
    issues: List[PatchIssue] = []

    result_names = set(result.sections)
    desired_names = set(desired.sections)

    sections_ok = result_names == desired_names
    checks.append(
        VerificationCheck(
            name="sections",
            ok=sections_ok,
            detail=(
                "Секции совпадают"
                if sections_ok
                else "Лишние: %s; отсутствуют: %s"
                % (
                    sorted(result_names - desired_names),
                    sorted(desired_names - result_names),
                )
            ),
        )
    )
    if not sections_ok:
        issues.append(
            PatchIssue(
                code="sections_mismatch",
                message="Набор секций после применения патча не совпал",
                severity="error",
            )
        )

    common = sorted(result_names & desired_names)

    for name in common:
        if changed_sections is not None and name not in changed_sections:
            continue

        result_section = result.sections[name]
        desired_section = desired.sections[name]
        result_view = result_section.view()
        desired_view = desired_section.view()

        values_ok = result_view.present == desired_view.present
        checks.append(
            VerificationCheck(
                name="values:%s" % name,
                ok=values_ok,
                detail=(
                    "Значения совпадают"
                    if values_ok
                    else "Ожидалось %r, получено %r"
                    % (desired_view.present, result_view.present)
                ),
            )
        )
        if not values_ok:
            issues.append(
                PatchIssue(
                    code="values_mismatch",
                    message="Значения секции [%s] не совпали" % name,
                    severity="error",
                    section=name,
                )
            )

        parents_ok = (
            result_section.effective_parents()
            == desired_section.effective_parents()
        )
        checks.append(
            VerificationCheck(
                name="parents:%s" % name,
                ok=parents_ok,
                detail=(
                    "Наследование совпадает"
                    if parents_ok
                    else "Ожидалось %r, получено %r"
                    % (
                        desired_section.effective_parents(),
                        result_section.effective_parents(),
                    )
                ),
            )
        )
        if not parents_ok:
            issues.append(
                PatchIssue(
                    code="parents_mismatch",
                    message="Наследование секции [%s] не совпало" % name,
                    severity="error",
                    section=name,
                )
            )

        ops_ok = result_view.list_ops == desired_view.list_ops
        checks.append(
            VerificationCheck(
                name="list_ops:%s" % name,
                ok=ops_ok,
                detail=(
                    "Операции со списками совпадают"
                    if ops_ok
                    else "Ожидалось %r, получено %r"
                    % (desired_view.list_ops, result_view.list_ops)
                ),
            )
        )
        if not ops_ok:
            issues.append(
                PatchIssue(
                    code="list_ops_mismatch",
                    message="Операции со списками в [%s] не совпали" % name,
                    severity="error",
                    section=name,
                )
            )

    return checks, issues


class ModelPatchVerifier(BasePatchVerifier):
    """Проверка патча на уровне модели документа."""

    allowed_configs = None
    allow_scripts = False

    def verify(self, generated: GeneratedPatch) -> VerificationResult:
        plan = generated.plan
        checks, issues = self.common_checks(generated)

        diff = plan.diff
        if diff is None:
            checks.append(
                VerificationCheck(
                    name="roundtrip",
                    ok=False,
                    detail="В плане нет исходного diff",
                )
            )
            issues.append(
                PatchIssue(
                    code="no_diff",
                    message="План не содержит исходного сравнения",
                    severity="error",
                )
            )
            return VerificationResult(
                ok=False,
                checks=tuple(checks),
                issues=tuple(issues),
            )

        patch_document = parse_ltx_document(
            generated.text,
            source_path=plan.source_path,
        )

        result, apply_issues = apply_patch_document(
            diff.before,
            patch_document,
        )
        issues.extend(apply_issues)

        changed_sections = {
            change.section
            for change in plan.changes
            if change.section
        }
        compare_checks, compare_issues = _compare_documents(
            result,
            diff.after,
            changed_sections=changed_sections or None,
        )
        checks.extend(compare_checks)
        issues.extend(compare_issues)

        ok = all(check.ok for check in checks) and not any(
            issue.severity == "error" for issue in issues
        )

        return VerificationResult(
            ok=ok,
            checks=tuple(checks),
            issues=tuple(issues),
        )


class ResolverPatchVerifier:
    """Проверка патча настоящим ``Resolver4`` без записи в VFS.

    Патч виртуально подмешивается в загрузку корня. Затем проверяется,
    что итоговые значения изменились именно так, как задумано планом.

    Проверяются ключи и секции. Изменение родителей проверяется только
    на уровне модели: итоговое влияние наследования на все ключи не
    сравнивается, чтобы не выдавать ложных ошибок.
    """

    def __init__(self, adapter=None):
        self.adapter = adapter

    def _norm(self, value):
        if value is None:
            return None
        if type(value).__name__ == "DLTXToken":
            return None
        return str(value)

    def verify(self, generated: GeneratedPatch) -> VerificationResult:
        checks: List[VerificationCheck] = []
        issues: List[PatchIssue] = []

        plan = generated.plan
        root = plan.target.root_path

        if self.adapter is None:
            checks.append(
                VerificationCheck(
                    name="resolver",
                    ok=False,
                    detail="Адаптер Resolver4 не передан",
                )
            )
            issues.append(
                PatchIssue(
                    code="resolver_missing",
                    message="Нет адаптера Resolver4 для round-trip проверки",
                    severity="error",
                )
            )
            return VerificationResult(
                ok=False,
                checks=tuple(checks),
                issues=tuple(issues),
            )

        if root is None:
            checks.append(
                VerificationCheck(
                    name="target",
                    ok=False,
                    detail="Не определён корень загрузки",
                )
            )
            issues.append(
                PatchIssue(
                    code="target_not_resolved",
                    message="Не определён корень загрузки для патча",
                    severity="error",
                )
            )
            return VerificationResult(
                ok=False,
                checks=tuple(checks),
                issues=tuple(issues),
            )

        if not generated.files:
            checks.append(
                VerificationCheck(
                    name="files",
                    ok=False,
                    detail="Нет файлов патча",
                )
            )
            issues.append(
                PatchIssue(
                    code="empty_patch",
                    message="Сгенерированный патч пуст",
                    severity="error",
                )
            )
            return VerificationResult(
                ok=False,
                checks=tuple(checks),
                issues=tuple(issues),
            )

        patch_filename = Path(generated.files[0].relative_path).name

        try:
            # Baseline-резолв не нужен: проверяются только изменения плана
            # в итоговом состоянии после подмешивания патча. Раньше здесь
            # выполнялся ещё один полный проход резолвера, результат
            # которого нигде не использовался — это удваивало время
            # проверки каждого патча.
            patched = self.adapter.resolve_with_virtual_patch(
                root,
                patch_filename,
                generated.text,
            )
        except Exception as exc:
            checks.append(
                VerificationCheck(
                    name="resolver",
                    ok=False,
                    detail=str(exc),
                )
            )
            issues.append(
                PatchIssue(
                    code="resolver_failed",
                    message="Resolver4 не смог проверить патч: %s" % exc,
                    severity="error",
                )
            )
            return VerificationResult(
                ok=False,
                checks=tuple(checks),
                issues=tuple(issues),
            )

        checks.append(
            VerificationCheck(
                name="resolver",
                ok=True,
                detail="Resolver4 выполнил baseline и патч",
            )
        )

        checked = 0

        for change in plan.changes:
            section = change.section
            key = change.key

            if change.kind in ("set_key", "add_key"):
                checked += 1
                actual = patched.get(section, {})
                ok = key in actual and self._norm(actual.get(key)) == self._norm(
                    change.new_value
                )
                checks.append(
                    VerificationCheck(
                        name="resolver:%s:%s" % (section, key),
                        ok=ok,
                        detail=(
                            "Значение совпало"
                            if ok
                            else "Ожидалось %r, получено %r"
                            % (change.new_value, actual.get(key))
                        ),
                    )
                )
                if not ok:
                    issues.append(
                        PatchIssue(
                            code="resolver_value_mismatch",
                            message=(
                                "После патча [%s] %s не равно ожидаемому"
                                % (section, key)
                            ),
                            severity="error",
                            section=section,
                            key=key,
                        )
                    )

            elif change.kind == "delete_key":
                checked += 1
                actual = patched.get(section, {})
                ok = key not in actual
                checks.append(
                    VerificationCheck(
                        name="resolver:delete:%s:%s" % (section, key),
                        ok=ok,
                        detail=(
                            "Ключ удалён"
                            if ok
                            else "Ключ всё ещё присутствует"
                        ),
                    )
                )
                if not ok:
                    issues.append(
                        PatchIssue(
                            code="resolver_delete_mismatch",
                            message=(
                                "После патча ключ '%s' в [%s] не удалён"
                                % (key, section)
                            ),
                            severity="error",
                            section=section,
                            key=key,
                        )
                    )

            elif change.kind in ("list_append", "list_remove"):
                checked += 1
                actual = patched.get(section, {})
                value = self._norm(actual.get(key))
                items = split_csv(value or "")
                item = self._norm(change.new_value) or ""
                if change.kind == "list_append":
                    ok = item in items
                else:
                    ok = item not in items

                checks.append(
                    VerificationCheck(
                        name="resolver:%s:%s:%s"
                        % (change.kind, section, key),
                        ok=ok,
                        detail=(
                            "Операция со списком применилась"
                            if ok
                            else "Ожидалось значение %r в списке %r"
                            % (item, items)
                        ),
                    )
                )
                if not ok:
                    issues.append(
                        PatchIssue(
                            code="resolver_list_mismatch",
                            message=(
                                "Операция %s для [%s] %s не дала ожидаемого "
                                "списка" % (change.kind, section, key)
                            ),
                            severity="error",
                            section=section,
                            key=key,
                        )
                    )

            elif change.kind == "delete_section":
                checked += 1
                ok = section not in patched
                checks.append(
                    VerificationCheck(
                        name="resolver:delete_section:%s" % section,
                        ok=ok,
                        detail=(
                            "Секция удалена"
                            if ok
                            else "Секция всё ещё присутствует"
                        ),
                    )
                )
                if not ok:
                    issues.append(
                        PatchIssue(
                            code="resolver_section_delete_mismatch",
                            message="После патча секция [%s] не удалена" % section,
                            severity="error",
                            section=section,
                        )
                    )

            elif change.kind == "add_section":
                checked += 1
                snapshot = change.section_snapshot
                actual = patched.get(section)
                ok = actual is not None

                if ok and snapshot is not None:
                    for key_name, expected in snapshot.values:
                        if key_name not in actual:
                            ok = False
                            break
                        if self._norm(actual.get(key_name)) != self._norm(expected):
                            ok = False
                            break

                checks.append(
                    VerificationCheck(
                        name="resolver:add_section:%s" % section,
                        ok=ok,
                        detail=(
                            "Секция создана"
                            if ok
                            else "Секция или её ключи не совпали"
                        ),
                    )
                )
                if not ok:
                    issues.append(
                        PatchIssue(
                            code="resolver_section_add_mismatch",
                            message="После патча секция [%s] не создана" % section,
                            severity="error",
                            section=section,
                        )
                    )

        if checked == 0:
            checks.append(
                VerificationCheck(
                    name="resolver:coverage",
                    ok=True,
                    detail=(
                        "В плане только изменения наследования; "
                        "они проверены моделью"
                    ),
                )
            )

        ok = all(check.ok for check in checks) and not any(
            issue.severity == "error" for issue in issues
        )

        return VerificationResult(
            ok=ok,
            checks=tuple(checks),
            issues=tuple(issues),
        )
