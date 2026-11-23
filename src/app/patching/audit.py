# -*- coding: utf-8 -*-
"""Файловый аудит успешных записей патч-мода."""

from __future__ import annotations

import datetime
import json
import threading
from pathlib import Path


class PatchAuditLog:
    """Audit-sink для ``PatchCommitService``: JSON-строка на запись."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()

    @classmethod
    def for_session(cls, directory):
        """Новый файл аудита для текущей сессии приложения."""
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = Path(directory) / ("patch-audit-%s.log" % stamp)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=False)
        except OSError:
            pass
        return cls(path)

    def __call__(self, result) -> None:
        record = {
            "timestamp": datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat(),
            "plan_id": str(result.plan_id),
            "snapshot_id": str(result.snapshot_id),
            "mod_dir": str(result.mod_dir),
            "paths": [str(path) for path in result.paths],
            "confirmed_risks": list(result.confirmed_risks),
            "status": str(getattr(result, "status", "committed")),
            "reason": str(getattr(result, "reason", "")),
            "roundtrip_ok": (
                None
                if result.roundtrip_report is None
                else bool(result.roundtrip_report.ok)
            ),
            "roundtrip_checks": (
                []
                if result.roundtrip_report is None
                else [
                    check.name
                    for check in result.roundtrip_report.checks
                ]
            ),
        }
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")


__all__ = ["PatchAuditLog"]
