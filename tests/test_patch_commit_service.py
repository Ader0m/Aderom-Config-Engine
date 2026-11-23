# -*- coding: utf-8 -*-
"""Единственная точка записи: авторизация, риски и post-write проверка."""

import os
import tempfile
import unittest
from dataclasses import dataclass, replace
from pathlib import Path
from unittest.mock import patch

from tests.support import SRC  # noqa: F401

from core.patching.commit import (
    CommitAuthorization,
    PatchCommitError,
    PatchCommitService,
)
from core.patching.roundtrip import (
    RoundTripReport,
    generated_fingerprint,
)
from core.patching.models import (
    GeneratedPatch,
    PatchFile,
    PatchIssue,
    PatchPlan,
    PatchTarget,
    VerificationResult,
)
from core.patching.writer import PatchWriteError, PatchWriter


@dataclass(frozen=True)
class _RiskChange:
    description: str
    risk: str = "confirm"


def _generated(risky=False, snapshot_id="SNAP-X"):
    source = Path("C:/game/gamedata/configs/a.ltx")
    changes = (_RiskChange("danger"),) if risky else ()
    plan = PatchPlan(
        source_path=source,
        target=PatchTarget(
            source_path=source,
            root_path=Path("C:/game/gamedata/configs/system.ltx"),
        ),
        patch_mod="Test",
        patch_file_rel=Path("mod_a.ltx"),
        changes=changes,
        snapshot_id=snapshot_id,
    )
    return GeneratedPatch(
        plan=plan,
        files=(
            PatchFile(
                relative_path=Path("gamedata/configs/mod_a.ltx"),
                text="[a]\nx = 1\n",
            ),
        ),
        text="[a]\nx = 1\n",
        verification=VerificationResult(ok=True),
    )


@dataclass(frozen=True)
class _Change:
    kind: str
    section: str = ""


class _BadWriter:
    def write(self, generated, patch_mod_root, **kwargs):
        path = Path(patch_mod_root) / generated.files[0].relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"bad")
        return (path,)


class _NoopRoundtrip:
    def __init__(self):
        self._token = object()

    def verify(self, generated):
        return VerificationResult(ok=True)


class PatchCommitServiceTest(unittest.TestCase):
    def _service(self):
        return PatchCommitService(
            PatchWriter(
                require_verification=True,
                require_resolver=False,
            ),
            _NoopRoundtrip(),
        )

    def test_authorized_write_and_audit(self):
        with tempfile.TemporaryDirectory() as d:
            records = []
            service = PatchCommitService(
                PatchWriter(
                    require_verification=True,
                    require_resolver=False,
                ),
                _NoopRoundtrip(),
                audit=records.append,
            )
            generated = _generated()
            authorization = service.authorize(generated)

            result = service.commit(
                generated,
                authorization,
                Path(d) / "patch_mod",
                expected_snapshot_id="SNAP-X",
            )

            self.assertEqual(result.plan_id, authorization.plan_id)
            self.assertEqual(result.snapshot_id, "SNAP-X")
            self.assertEqual(result.confirmed_risks, ())
            self.assertIsNotNone(result.roundtrip_report)
            self.assertEqual(len(result.paths), 1)
            self.assertEqual(
                result.paths[0].read_text(encoding="windows-1251"),
                "[a]\nx = 1\n",
            )
            self.assertEqual(records, [result])

    def test_write_without_authorization_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            service = self._service()
            with self.assertRaises(PatchCommitError):
                service.commit(
                    _generated(),
                    None,
                    Path(d) / "patch_mod",
                    expected_snapshot_id="SNAP-X",
                )

    def test_manually_created_authorization_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            service = self._service()
            generated = _generated()
            forged = CommitAuthorization(
                plan_id=service.plan_id(generated),
                snapshot_id="SNAP-X",
                confirmed_risks=(),
                _token=None,
            )
            with self.assertRaises(PatchCommitError):
                service.commit(
                    generated,
                    forged,
                    Path(d) / "patch_mod",
                    expected_snapshot_id="SNAP-X",
                )

    def test_authorization_from_another_service_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            first = self._service()
            second = self._service()
            generated = _generated()
            authorization = first.authorize(generated)
            with self.assertRaises(PatchCommitError):
                second.commit(
                    generated,
                    authorization,
                    Path(d) / "patch_mod",
                    expected_snapshot_id="SNAP-X",
                )

    def test_risky_plan_requires_confirmed_risks(self):
        with tempfile.TemporaryDirectory() as d:
            service = self._service()
            generated = _generated(risky=True)

            with self.assertRaises(PatchCommitError) as ctx:
                service.authorize(generated)
            self.assertIn("danger", str(ctx.exception))

            authorization = service.authorize(
                generated,
                confirmed_risks=("danger",),
            )
            result = service.commit(
                generated,
                authorization,
                Path(d) / "patch_mod",
                expected_snapshot_id="SNAP-X",
            )
            self.assertEqual(result.confirmed_risks, ("danger",))
            self.assertEqual(len(result.paths), 1)

    def test_snapshot_is_bound_to_authorization(self):
        with tempfile.TemporaryDirectory() as d:
            service = self._service()
            generated = _generated()
            authorization = service.authorize(generated)
            changed = replace(
                generated,
                plan=replace(generated.plan, snapshot_id="SNAP-Y"),
            )
            with self.assertRaises(PatchCommitError):
                service.commit(
                    changed,
                    authorization,
                    Path(d) / "patch_mod",
                    expected_snapshot_id="SNAP-X",
                )

    def test_unverified_patch_is_not_authorized(self):
        service = self._service()
        generated = replace(
            _generated(),
            verification=VerificationResult(ok=False),
        )
        with self.assertRaises(PatchCommitError):
            service.authorize(generated)

    def test_plan_with_errors_is_not_authorized(self):
        service = self._service()
        generated = _generated()
        generated = replace(
            generated,
            plan=replace(
                generated.plan,
                issues=(
                    PatchIssue(
                        code="test_error",
                        message="Ошибка плана",
                        severity="error",
                    ),
                ),
            ),
        )
        with self.assertRaises(PatchCommitError):
            service.authorize(generated)

    def test_empty_snapshot_is_not_authorized(self):
        service = self._service()
        with self.assertRaises(PatchCommitError):
            service.authorize(_generated(snapshot_id=""))

    def test_expected_snapshot_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            service = self._service()
            generated = _generated()
            authorization = service.authorize(generated)
            with self.assertRaises(PatchCommitError):
                service.commit(
                    generated,
                    authorization,
                    Path(d) / "patch_mod",
                    expected_snapshot_id="SNAP-Y",
                )

    def test_precomputed_roundtrip_report_is_reused(self):
        class _CountingRoundtrip:
            def __init__(self):
                self.calls = 0
                self._token = object()

            def verify(self, generated):
                self.calls += 1
                return VerificationResult(ok=True)

        verifier = _CountingRoundtrip()
        service = PatchCommitService(
            PatchWriter(
                require_verification=True,
                require_resolver=False,
            ),
            verifier,
        )
        generated = _generated()
        report = RoundTripReport(
            verification=VerificationResult(ok=True),
            plan_id=generated_fingerprint(generated),
            snapshot_id="SNAP-X",
            _token=verifier._token,
        )

        authorization = service.authorize(
            generated,
            roundtrip_report=report,
        )

        self.assertEqual(verifier.calls, 0)
        self.assertIs(authorization.roundtrip_report, report.verification)

    def test_forged_roundtrip_report_is_rejected(self):
        service = self._service()
        with self.assertRaises(PatchCommitError):
            service.authorize(
                _generated(),
                roundtrip_report=VerificationResult(ok=True),
            )

    def _report_for(self, service, generated):
        return RoundTripReport(
            verification=VerificationResult(ok=True),
            plan_id=generated_fingerprint(generated),
            snapshot_id=str(generated.plan.snapshot_id or ""),
            _token=service.roundtrip._token,
        )

    def test_roundtrip_report_from_other_target_root_is_rejected(self):
        service = self._service()
        generated = _generated()
        report = self._report_for(service, generated)
        changed = replace(
            generated,
            plan=replace(
                generated.plan,
                target=replace(
                    generated.plan.target,
                    root_path=Path("C:/game/gamedata/configs/other.ltx"),
                ),
            ),
        )

        with self.assertRaises(PatchCommitError):
            service.authorize(changed, roundtrip_report=report)

    def test_roundtrip_report_from_other_patch_text_is_rejected(self):
        service = self._service()
        generated = _generated()
        report = self._report_for(service, generated)
        changed = replace(
            generated,
            text=generated.text + "; changed\n",
        )

        with self.assertRaises(PatchCommitError):
            service.authorize(changed, roundtrip_report=report)

    def test_roundtrip_report_from_other_changes_is_rejected(self):
        service = self._service()
        generated = _generated()
        report = self._report_for(service, generated)
        changed = replace(
            generated,
            plan=replace(
                generated.plan,
                changes=(_Change("set_key", "w"),),
            ),
        )

        with self.assertRaises(PatchCommitError):
            service.authorize(changed, roundtrip_report=report)

    def test_roundtrip_report_from_other_plan_is_rejected(self):
        class _Verifier:
            def __init__(self):
                self._token = object()

            def verify(self, generated):
                return VerificationResult(ok=True)

        verifier = _Verifier()
        service = PatchCommitService(
            PatchWriter(
                require_verification=True,
                require_resolver=False,
            ),
            verifier,
        )
        generated = _generated()
        report = RoundTripReport(
            verification=VerificationResult(ok=True),
            plan_id="other-plan",
            snapshot_id="SNAP-X",
            _token=verifier._token,
        )
        with self.assertRaises(PatchCommitError):
            service.authorize(
                generated,
                roundtrip_report=report,
            )

    def test_roundtrip_report_from_other_snapshot_is_rejected(self):
        service = self._service()
        generated = _generated()
        report = RoundTripReport(
            verification=VerificationResult(ok=True),
            plan_id=generated_fingerprint(generated),
            snapshot_id="SNAP-OTHER",
            _token=service.roundtrip._token,
        )
        with self.assertRaises(PatchCommitError):
            service.authorize(
                generated,
                roundtrip_report=report,
            )

    def test_roundtrip_report_from_other_verifier_is_rejected(self):
        first = PatchCommitService(
            PatchWriter(
                require_verification=True,
                require_resolver=False,
            ),
            _NoopRoundtrip(),
        )
        second = PatchCommitService(
            PatchWriter(
                require_verification=True,
                require_resolver=False,
            ),
            _NoopRoundtrip(),
        )
        generated = _generated()
        report = RoundTripReport(
            verification=VerificationResult(ok=True),
            plan_id=generated_fingerprint(generated),
            snapshot_id="SNAP-X",
            _token=first.roundtrip._token,
        )
        with self.assertRaises(PatchCommitError):
            second.authorize(
                generated,
                roundtrip_report=report,
            )

    def test_audit_outcome_marks_rollback(self):
        with tempfile.TemporaryDirectory() as d:
            records = []
            service = PatchCommitService(
                PatchWriter(
                    require_verification=True,
                    require_resolver=False,
                ),
                _NoopRoundtrip(),
                audit=records.append,
            )
            generated = _generated()
            authorization = service.authorize(generated)
            result = service.commit(
                generated,
                authorization,
                Path(d) / "patch_mod",
                expected_snapshot_id="SNAP-X",
            )

            service.audit_outcome(
                result,
                "rolled_back",
                "cancelled",
            )

            self.assertEqual(records[-1].status, "rolled_back")
            self.assertEqual(records[-1].reason, "cancelled")

    def test_roundtrip_failure_blocks_authorization(self):
        class _Roundtrip:
            def verify(self, generated):
                return VerificationResult(
                    ok=False,
                    issues=(
                        PatchIssue(
                            code="roundtrip_failed",
                            message="round-trip failed",
                            severity="error",
                        ),
                    ),
                )

        service = PatchCommitService(
            PatchWriter(
                require_verification=True,
                require_resolver=False,
            ),
            roundtrip=_Roundtrip(),
        )
        with self.assertRaises(PatchCommitError) as ctx:
            service.authorize(_generated())
        self.assertIn("Round-trip", str(ctx.exception))

    def test_writer_rejects_plan_errors_and_empty_snapshot(self):
        writer = PatchWriter(
            require_verification=True,
            require_resolver=False,
        )
        with tempfile.TemporaryDirectory() as d:
            generated = _generated()
            with self.assertRaises(PatchWriteError):
                writer.write(
                    replace(
                        generated,
                        plan=replace(
                            generated.plan,
                            issues=(
                                PatchIssue(
                                    code="test_error",
                                    message="Ошибка плана",
                                    severity="error",
                                ),
                            ),
                        ),
                    ),
                    Path(d) / "errors",
                )
            with self.assertRaises(PatchWriteError):
                writer.write(
                    _generated(snapshot_id=""),
                    Path(d) / "no_snapshot",
                )

    def test_post_write_verification_detects_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            service = PatchCommitService(
                _BadWriter(),
                _NoopRoundtrip(),
            )
            generated = _generated()
            authorization = service.authorize(generated)
            path = (
                Path(d)
                / "patch_mod"
                / generated.files[0].relative_path
            )
            with self.assertRaises(PatchCommitError):
                service.commit(
                    generated,
                    authorization,
                    Path(d) / "patch_mod",
                    expected_snapshot_id="SNAP-X",
                )
            self.assertFalse(path.exists())

    def test_partial_write_is_rolled_back(self):
        with tempfile.TemporaryDirectory() as d:
            service = self._service()
            generated = replace(
                _generated(),
                files=(
                    PatchFile(
                        relative_path=Path("gamedata/configs/a.ltx"),
                        text="a",
                    ),
                    PatchFile(
                        relative_path=Path("gamedata/configs/b.ltx"),
                        text="b",
                    ),
                ),
            )
            authorization = service.authorize(generated)
            real_replace = os.replace
            calls = {"count": 0}

            def flaky_replace(src, dst):
                calls["count"] += 1
                if calls["count"] == 2:
                    raise OSError("boom")
                return real_replace(src, dst)

            with patch(
                "core.patching.writer.os.replace",
                side_effect=flaky_replace,
            ):
                with self.assertRaises(OSError):
                    service.commit(
                        generated,
                        authorization,
                        Path(d) / "patch_mod",
                        expected_snapshot_id="SNAP-X",
                    )

            self.assertFalse(
                (Path(d) / "patch_mod" / "gamedata" / "configs" / "a.ltx").exists()
            )


if __name__ == "__main__":
    unittest.main()
