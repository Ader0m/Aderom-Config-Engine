# -*- coding: utf-8 -*-
"""Единственная точка записи сгенерированного патча.

``PatchCommitService`` связывает проверенный патч, снимок и явное
подтверждение рисков. Запись без ``CommitAuthorization`` невозможна;
токен выпускает только сервис. ``PatchWriter`` остаётся низкоуровневым
примитивом внутри core.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterable, Optional, Tuple

from core.patching.models import (
    GeneratedPatch,
    PatchPlan,
    VerificationResult,
)
from core.patching.roundtrip import RoundTripReport, generated_fingerprint
from core.patching.writer import PatchWriter


logger = logging.getLogger(__name__)


class PatchCommitError(RuntimeError):
    """Патч нельзя авторизовать или записать."""


@dataclass(frozen=True)
class CommitAuthorization:
    """Разрешение на запись конкретного патча и снимка.

    ``_token`` выпускается только :class:`PatchCommitService`; вручную
    созданный объект с другим токеном отклоняется.
    """

    plan_id: str
    snapshot_id: str
    confirmed_risks: Tuple[str, ...] = ()
    roundtrip_report: Optional[VerificationResult] = field(
        default=None,
        repr=False,
        compare=False,
    )
    _token: object = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class PatchCommitResult:
    """Аудит одной авторизованной записи."""

    mod_dir: Path
    paths: Tuple[Path, ...]
    plan_id: str
    snapshot_id: str
    confirmed_risks: Tuple[str, ...] = ()
    roundtrip_report: Optional[VerificationResult] = None
    status: str = "committed"
    reason: str = ""


def _risk_key(change) -> str:
    """Человекочитаемый ключ опасного изменения."""
    description = str(getattr(change, "description", "") or "").strip()
    if description:
        return description
    parts = (
        str(getattr(change, "kind", "") or "change"),
        str(getattr(change, "section", "") or ""),
        str(getattr(change, "key", "") or ""),
        str(getattr(change, "parent", "") or ""),
        str(getattr(change, "path", "") or ""),
        str(getattr(change, "note", "") or ""),
    )
    text = " | ".join(part for part in parts if part)
    return text or "опасное изменение"


class PatchCommitService:
    """Авторизация и запись патча через ``PatchWriter``."""

    def __init__(
        self,
        writer: PatchWriter,
        roundtrip,
        post_write_verification: bool = True,
        audit: Optional[Callable[[PatchCommitResult], None]] = None,
    ):
        if writer is None:
            raise ValueError("PatchCommitService требует PatchWriter")
        if roundtrip is None:
            raise ValueError(
                "PatchCommitService требует round-trip verifier"
            )
        self.writer = writer
        self.roundtrip = roundtrip
        self.post_write_verification = bool(post_write_verification)
        self.audit = audit
        self._token = object()

    # -------------------------------------------------- риски
    @staticmethod
    def risk_keys(plan: PatchPlan) -> Tuple[str, ...]:
        """Ключи опасных изменений; пусто — подтверждение не нужно."""
        keys = [
            _risk_key(change)
            for change in plan.changes
            if getattr(change, "risk", "") == "confirm"
        ]
        return tuple(dict.fromkeys(keys))

    # -------------------------------------------------- авторизация
    @staticmethod
    def _check_generated(generated: GeneratedPatch) -> None:
        if generated is None:
            raise PatchCommitError("Нет сгенерированного патча")
        if not generated.files:
            raise PatchCommitError("Патч не содержит файлов")
        plan = generated.plan
        if plan.has_errors:
            raise PatchCommitError(
                "План содержит ошибки: %s"
                % "; ".join(issue.message for issue in plan.errors())
            )
        if not str(plan.snapshot_id or ""):
            raise PatchCommitError(
                "План не привязан к снимку VFS: запись запрещена"
            )
        if generated.verification is None or not generated.verification.ok:
            raise PatchCommitError(
                "Патч не проверен: авторизация записи запрещена"
            )

    def plan_id(self, generated: GeneratedPatch) -> str:
        """Стабильный идентификатор плана и содержимого патча."""
        return generated_fingerprint(generated)

    def authorize(
        self,
        generated: GeneratedPatch,
        confirmed_risks: Iterable[str] = (),
        roundtrip_report: Optional[RoundTripReport] = None,
    ) -> CommitAuthorization:
        """Выпустить разрешение, если все опасные изменения подтверждены.

        ``roundtrip_report`` — доверенный отчёт того же verifier'а,
        полученный для этого же сгенерированного патча и снимка.
        """
        self._check_generated(generated)
        if roundtrip_report is not None:
            if not isinstance(roundtrip_report, RoundTripReport):
                raise PatchCommitError(
                    "roundtrip_report должен быть доверенным "
                    "RoundTripReport"
                )
            if roundtrip_report._token is not self.roundtrip._token:
                raise PatchCommitError(
                    "RoundTripReport выпущен другим verifier'ом"
                )
            if roundtrip_report.plan_id != self.plan_id(generated):
                raise PatchCommitError(
                    "RoundTripReport относится к другому патчу"
                )
            if roundtrip_report.snapshot_id != str(
                generated.plan.snapshot_id or ""
            ):
                raise PatchCommitError(
                    "RoundTripReport относится к другому снимку"
                )
            report = roundtrip_report.verification
        else:
            report = self.roundtrip.verify(generated)
        if not report.ok:
            errors = [
                issue.message
                for issue in report.issues
                if issue.severity == "error"
            ]
            if not errors:
                errors = [
                    check.detail or check.name
                    for check in report.failed_checks
                ]
            raise PatchCommitError(
                "Round-trip не пройден: %s"
                % "; ".join(errors[:20] or ["см. проверку"])
            )
        risks = self.risk_keys(generated.plan)
        confirmed = tuple(str(item) for item in (confirmed_risks or ()))
        missing = [risk for risk in risks if risk not in confirmed]
        if missing:
            raise PatchCommitError(
                "Требуется подтверждение опасных изменений: %s"
                % "; ".join(missing)
            )
        return CommitAuthorization(
            plan_id=self.plan_id(generated),
            snapshot_id=str(generated.plan.snapshot_id or ""),
            confirmed_risks=tuple(
                risk for risk in risks if risk in confirmed
            ),
            roundtrip_report=report,
            _token=self._token,
        )

    def _check_authorization(
        self,
        generated: GeneratedPatch,
        authorization,
    ) -> None:
        if not isinstance(authorization, CommitAuthorization):
            raise PatchCommitError(
                "Запись без CommitAuthorization запрещена"
            )
        if authorization._token is not self._token:
            raise PatchCommitError(
                "CommitAuthorization выпущен другим сервисом"
            )
        if authorization.plan_id != self.plan_id(generated):
            raise PatchCommitError(
                "CommitAuthorization относится к другому плану"
            )
        if authorization.snapshot_id != str(
            generated.plan.snapshot_id or ""
        ):
            raise PatchCommitError(
                "Снимок плана изменился после авторизации"
            )
        risks = self.risk_keys(generated.plan)
        missing = [
            risk for risk in risks
            if risk not in authorization.confirmed_risks
        ]
        if missing:
            raise PatchCommitError(
                "Опасные изменения не подтверждены: %s"
                % "; ".join(missing)
            )

    # -------------------------------------------------- запись
    def commit(
        self,
        generated: GeneratedPatch,
        authorization: CommitAuthorization,
        patch_mod_root,
        *,
        expected_snapshot_id,
        forbidden_roots=(),
        overwrite: bool = False,
        asset_rules=None,
    ) -> PatchCommitResult:
        """Записать патч. ``PatchWriter`` вызывается только отсюда.

        ``expected_snapshot_id`` — обязательный текущий снимок VFS.
        Запись устаревшего или несвязанного плана отклоняется.
        """
        self._check_generated(generated)
        self._check_authorization(generated, authorization)
        current = str(expected_snapshot_id or "")
        if not current:
            raise PatchCommitError(
                "expected_snapshot_id обязателен для записи"
            )
        if current != str(generated.plan.snapshot_id or ""):
            raise PatchCommitError(
                "Снимок VFS изменился: план построен для %s, "
                "текущий %s"
                % (generated.plan.snapshot_id, current)
            )

        plan = generated.plan
        if plan.needs_confirmation:
            # Единственное место, где выставляется legacy-флаг writer'а:
            # только после проверки CommitAuthorization.
            plan = replace(plan, confirmed=True)
            generated = replace(generated, plan=plan)

        paths = self.writer.write(
            generated,
            patch_mod_root,
            forbidden_roots=tuple(forbidden_roots or ()),
            overwrite=overwrite,
            asset_rules=asset_rules,
        )
        if self.post_write_verification:
            try:
                self._verify_written(generated, paths)
            except Exception:
                if not overwrite:
                    for path in paths:
                        try:
                            if Path(path).exists():
                                Path(path).unlink()
                        except OSError:
                            pass
                raise

        result = PatchCommitResult(
            mod_dir=Path(patch_mod_root),
            paths=tuple(paths),
            plan_id=authorization.plan_id,
            snapshot_id=authorization.snapshot_id,
            confirmed_risks=authorization.confirmed_risks,
            roundtrip_report=authorization.roundtrip_report,
        )
        self._emit_audit(result)
        return result

    def _emit_audit(self, result: PatchCommitResult) -> None:
        if self.audit is not None:
            try:
                self.audit(result)
            except Exception:
                logger.exception("Patch commit audit sink failed")
            return
        logger.info(
            "Patch %s: plan=%s snapshot=%s mod=%s files=%d risks=%s "
            "reason=%s",
            result.status,
            result.plan_id,
            result.snapshot_id,
            result.mod_dir,
            len(result.paths),
            ",".join(result.confirmed_risks),
            result.reason,
        )

    def audit_outcome(
        self,
        result: PatchCommitResult,
        status: str,
        reason: str = "",
    ) -> None:
        """Записать в аудит откат/отмену уже записанного результата."""
        self._emit_audit(
            replace(
                result,
                status=str(status),
                reason=str(reason or ""),
            )
        )

    @staticmethod
    def _verify_written(
        generated: GeneratedPatch,
        paths,
    ) -> None:
        """Прочитать записанные файлы и сверить с патчем."""
        paths = tuple(paths)
        if len(paths) != len(generated.files):
            raise PatchCommitError(
                "Post-write verification: число файлов не совпадает"
            )
        for path, file in zip(paths, generated.files):
            path = Path(path)
            if not path.is_file():
                raise PatchCommitError(
                    "Post-write verification: файл не найден: %s" % path
                )
            if file.data is not None:
                expected = bytes(file.data)
            else:
                expected = file.text.encode(
                    file.encoding,
                    errors="strict",
                )
            if path.read_bytes() != expected:
                raise PatchCommitError(
                    "Post-write verification не совпала: %s" % path
                )


__all__ = [
    "CommitAuthorization",
    "PatchCommitError",
    "PatchCommitResult",
    "PatchCommitService",
]
