# -*- coding: utf-8 -*-
"""TaskApiAdapter: фоновые задачи платформы."""

from __future__ import annotations

from ui.operations import normalize_operation_key
from ui.tasks import current_task

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

    def run(
        self,
        fn,
        on_done=None,
        on_log=None,
        operation_key=None,
        title=None,
        depends_on=(),
        priority=0,
    ):
        kwargs = {
            "on_done": on_done,
            "on_log": on_log,
            "owner": self._owner,
        }
        if operation_key is not None:
            kwargs["operation_key"] = operation_key
        if title is not None:
            kwargs["title"] = title
        if depends_on:
            kwargs["depends_on"] = depends_on
        if priority:
            kwargs["priority"] = priority
        return self._service.run(fn, **kwargs)

    def progress(self, done=None, total=None, message=None):
        handle = current_task()
        if handle is None:
            return False
        if self._owner and handle.owner and handle.owner != self._owner:
            return False
        handle.progress(done=done, total=total, message=message)
        return True

    def cancel_owner(self):
        return self._service.cancel_owner(self._owner)

    def operation_active(self, operation_key):
        key = normalize_operation_key(self._owner, operation_key)
        return bool(self._service.operations.is_active(key))

    def wait_owner(self, timeout_ms=5000):
        """Дождаться задач владельца (только для фонового потока)."""
        return self._service.wait_owner(self._owner, timeout_ms=timeout_ms)
