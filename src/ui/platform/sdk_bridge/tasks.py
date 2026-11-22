# -*- coding: utf-8 -*-
"""TaskApiAdapter: фоновые задачи платформы."""

from __future__ import annotations

from .common import _SDK_API_MARKER


class TaskApiAdapter:
    """Реализация :class:`sdk.api.TaskApi` поверх сервиса фоновых задач."""

    _sdk_api_kind = "tasks"

    def __init__(self, service):
        self._service = service
        self._owner = ""

    def bind_owner(self, owner):
        self._owner = str(owner or "")

    @classmethod
    def wrap(cls, service):
        if getattr(service, _SDK_API_MARKER, None) == cls._sdk_api_kind:
            return service
        return cls(service)

    def run(self, fn, on_done=None, on_log=None):
        return self._service.run(
            fn,
            on_done=on_done,
            on_log=on_log,
            owner=self._owner,
        )

    def cancel_owner(self):
        return self._service.cancel_owner(self._owner)

    def wait_owner(self, timeout_ms=5000):
        return self._service.wait_owner(self._owner, timeout_ms=timeout_ms)
