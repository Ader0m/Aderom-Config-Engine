# -*- coding: utf-8 -*-
"""Платформенный реестр операций.

Реестр хранит лёгкие снимки состояния, а не результаты и не тексты.
Он потокобезопасен: фоновые задачи обновляют прогресс, UI читает снимки
по таймеру. Отмена — кооперативная: реестр только просит операцию
остановиться.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Callable, Optional, Tuple

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"

ACTIVE_STATES = frozenset((QUEUED, RUNNING))
FINAL_STATES = frozenset((DONE, FAILED, CANCELLED))
ALL_STATES = ACTIVE_STATES | FINAL_STATES

# Ключи с этими префиксами общие для платформы и модулей.
GLOBAL_OPERATION_PREFIXES = ("vfs.", "project.", "platform.")
DEFAULT_MAX_KNOWN = 2000


def normalize_operation_key(owner, key):
    """Нормализовать ключ операции с учётом owner.

    Глобальные ключи (``vfs.*``, ``project.*``, ``platform.*``) остаются
    как есть. Ключи модуля получают префикс ``<owner>:``, чтобы два
    модуля не схлопнули разные операции в одну. Ключ, уже начинающийся с
    ``<owner>:`` или ``<owner>.``, не дублируется.
    """
    owner = str(owner or "")
    key = str(key or "")
    if not owner or not key:
        return key
    if key.startswith(GLOBAL_OPERATION_PREFIXES):
        return key
    if key.startswith(owner + ":") or key.startswith(owner + "."):
        return key
    return "%s:%s" % (owner, key)

STATE_LABELS = {
    QUEUED: "в очереди",
    RUNNING: "выполняется",
    DONE: "готово",
    FAILED: "ошибка",
    CANCELLED: "отменено",
}


@dataclass(frozen=True)
class OperationSnapshot:
    """Неизменяемое состояние одной операции для UI и диагностики."""

    key: str
    owner: str = ""
    title: str = ""
    state: str = QUEUED
    done: int = 0
    total: int = 0
    message: str = ""
    error: str = ""
    dependencies: Tuple[str, ...] = ()
    duplicate_count: int = 0
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def is_active(self) -> bool:
        return self.state in ACTIVE_STATES

    @property
    def is_finished(self) -> bool:
        return self.state in FINAL_STATES

    @property
    def progress(self) -> float:
        if self.total <= 0:
            return 0.0
        return max(0.0, min(1.0, self.done / float(self.total)))

    @property
    def state_label(self) -> str:
        return STATE_LABELS.get(self.state, self.state)

    @property
    def display_title(self) -> str:
        return self.title or self.key

    def with_message(self, message):
        return replace(self, message=str(message or ""))


class _Entry:
    __slots__ = ("snapshot", "cancel")

    def __init__(self, snapshot, cancel=None):
        self.snapshot = snapshot
        self.cancel = cancel


class OperationRegistry:
    """Общий список операций: задачи, предсборка и внешние процессы."""

    def __init__(self, max_finished=50, max_known=None):
        self._max_finished = max(1, int(max_finished))
        self._lock = threading.RLock()
        self._entries = {}
        self._finished_listeners = []
        self._known_states = OrderedDict()
        if max_known is None:
            max_known = max(DEFAULT_MAX_KNOWN, self._max_finished * 20)
        self._max_known = max(1, int(max_known))

    # -------------------------------------------------- подписка
    def add_finished_listener(self, callback):
        """Вызывать ``callback(snapshot)`` при переходе в done/failed/cancelled."""
        if not callable(callback):
            raise TypeError("finished listener должен быть callable")
        with self._lock:
            if callback not in self._finished_listeners:
                self._finished_listeners.append(callback)

    def remove_finished_listener(self, callback):
        with self._lock:
            try:
                self._finished_listeners.remove(callback)
            except ValueError:
                return False
        return True

    def _listeners_locked(self):
        return tuple(self._finished_listeners)

    @staticmethod
    def _notify(listeners, snapshot):
        for callback in listeners:
            try:
                callback(snapshot)
            except Exception:
                pass

    # -------------------------------------------------- запись
    def register(self, snapshot, cancel=None):
        """Добавить/заменить операцию и её обработчик отмены."""
        snapshot = self._normalize(snapshot)
        with self._lock:
            previous = self._entries.get(snapshot.key)
            self._entries[snapshot.key] = _Entry(snapshot, cancel)
            self._remember_key_locked(snapshot.key, snapshot.state)
            listeners = ()
            if snapshot.is_finished and not (
                previous is not None
                and previous.snapshot.is_finished
            ):
                listeners = self._listeners_locked()
            self._trim_locked()
        self._notify(listeners, snapshot)
        return snapshot

    def update(self, snapshot):
        """Обновить состояние уже известной операции."""
        snapshot = self._normalize(snapshot)
        with self._lock:
            entry = self._entries.get(snapshot.key)
            previous = entry.snapshot if entry is not None else None
            if entry is None:
                self._entries[snapshot.key] = _Entry(snapshot, None)
            else:
                entry.snapshot = snapshot
            self._remember_key_locked(snapshot.key, snapshot.state)
            listeners = ()
            if snapshot.is_finished and not (
                previous is not None and previous.is_finished
            ):
                listeners = self._listeners_locked()
            self._trim_locked()
        self._notify(listeners, snapshot)
        return snapshot

    def finish(self, snapshot, notify=True):
        """Зафиксировать завершённое состояние и снять cancel-обработчик.

        ``notify=False`` позволяет сначала выполнить пользовательский
        ``on_done``, а затем разбудить зависимые операции.
        """
        snapshot = self._normalize(snapshot)
        if snapshot.state not in FINAL_STATES:
            raise ValueError("finish() требует завершённого состояния")
        with self._lock:
            previous = self._entries.get(snapshot.key)
            self._entries[snapshot.key] = _Entry(snapshot, None)
            self._remember_key_locked(snapshot.key, snapshot.state)
            listeners = ()
            if notify and not (
                previous is not None
                and previous.snapshot.is_finished
            ):
                listeners = self._listeners_locked()
            self._trim_locked()
        self._notify(listeners, snapshot)
        return snapshot

    def notify_finished(self, snapshot):
        """Разослать уведомление о завершённой операции.

        Используется, когда запись финального состояния и side effects
        ``on_done`` должны идти до пробуждения зависимых задач.
        """
        snapshot = self._normalize(snapshot)
        if snapshot.state not in FINAL_STATES:
            raise ValueError(
                "notify_finished() требует завершённого состояния"
            )
        with self._lock:
            listeners = self._listeners_locked()
        self._notify(listeners, snapshot)
        return snapshot

    def upsert_external(
        self,
        key,
        *,
        owner="",
        title="",
        state=RUNNING,
        done=0,
        total=0,
        message="",
        error="",
        cancel=None,
    ):
        """Обновить операцию, которой владеет не TaskService (например,
        предсборка резолвера).

        Сохраняет исходное ``started_at`` и не сбрасывает счётчик
        повторных запросов.
        """
        key = str(key or "")
        if not key:
            raise ValueError("operation key не может быть пустым")
        state = str(state or RUNNING)
        if state not in ALL_STATES:
            raise ValueError("неизвестное состояние операции: %s" % state)

        now = time.time()
        with self._lock:
            previous = self._entries.get(key)
            previous_snapshot = (
                previous.snapshot if previous is not None else None
            )
            if previous_snapshot is not None:
                started_at = previous_snapshot.started_at
                duplicates = previous_snapshot.duplicate_count
                previous_cancel = previous.cancel
                if state == RUNNING and previous_snapshot.is_finished:
                    started_at = now
            else:
                started_at = now if state != QUEUED else 0.0
                duplicates = 0
                previous_cancel = None
            snapshot = OperationSnapshot(
                key=key,
                owner=str(owner or ""),
                title=str(
                    title
                    or (
                        previous_snapshot.title
                        if previous_snapshot is not None
                        else ""
                    )
                    or key
                ),
                state=state,
                done=int(done or 0),
                total=int(total or 0),
                message=str(message or ""),
                error=str(error or ""),
                dependencies=(
                    previous_snapshot.dependencies
                    if previous_snapshot is not None
                    else ()
                ),
                duplicate_count=duplicates,
                started_at=started_at,
                finished_at=now if state in FINAL_STATES else 0.0,
            )
            self._entries[key] = _Entry(
                snapshot,
                None
                if state in FINAL_STATES
                else (cancel if cancel is not None else previous_cancel),
            )
            self._remember_key_locked(key, state)
            listeners = ()
            if state in FINAL_STATES and not (
                previous_snapshot is not None
                and previous_snapshot.is_finished
            ):
                listeners = self._listeners_locked()
            self._trim_locked()
        self._notify(listeners, snapshot)
        return snapshot

    # -------------------------------------------------- чтение
    def get(self, key):
        with self._lock:
            entry = self._entries.get(str(key or ""))
            return entry.snapshot if entry is not None else None

    def has_seen(self, key):
        """Регистрировалась ли операция с таким ключом в этой сессии."""
        with self._lock:
            return str(key or "") in self._known_states

    def last_state(self, key):
        """Последнее известное состояние операции (включая вытесненную)."""
        key = str(key or "")
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                return entry.snapshot.state
            return self._known_states.get(key, "")

    def _remember_key_locked(self, key, state):
        key = str(key or "")
        if not key:
            return
        if key in self._known_states:
            del self._known_states[key]
        self._known_states[key] = str(state or "")
        self._trim_known_locked()

    def _protected_keys_locked(self):
        protected = {
            dependency
            for entry in self._entries.values()
            if entry.snapshot.is_active
            for dependency in entry.snapshot.dependencies
        }
        protected.update(
            entry.snapshot.key
            for entry in self._entries.values()
            if entry.snapshot.is_active
        )
        return protected

    def _trim_known_locked(self):
        if len(self._known_states) <= self._max_known:
            return
        protected = self._protected_keys_locked()
        candidates = [
            key
            for key in self._known_states
            if key not in protected
        ]
        priority = {
            DONE: 0,
            FAILED: 2,
            CANCELLED: 2,
        }
        candidates.sort(
            key=lambda key: priority.get(
                self._known_states.get(key, ""),
                1,
            )
        )
        for key in candidates:
            if len(self._known_states) <= self._max_known:
                break
            self._known_states.pop(key, None)

    def is_active(self, key):
        snapshot = self.get(key)
        return snapshot is not None and snapshot.is_active

    def active(self, owner=None):
        wanted = None if owner is None else str(owner or "")
        with self._lock:
            result = [
                entry.snapshot
                for entry in self._entries.values()
                if entry.snapshot.is_active
                and (wanted is None or entry.snapshot.owner == wanted)
            ]
        return tuple(sorted(result, key=lambda item: item.started_at or 0.0))

    def snapshots(self, include_finished=True):
        with self._lock:
            result = [
                entry.snapshot
                for entry in self._entries.values()
                if include_finished or entry.snapshot.is_active
            ]
        active = [item for item in result if item.is_active]
        finished = [item for item in result if item.is_finished]
        active.sort(key=lambda item: item.started_at or 0.0)
        finished.sort(
            key=lambda item: item.finished_at or item.started_at or 0.0,
            reverse=True,
        )
        return tuple(active + finished)

    def has_active(self, owner=None):
        return bool(self.active(owner))

    # -------------------------------------------------- отмена / очистка
    def cancel(self, key):
        """Попросить операцию остановиться. Возвращает ``True``, если она
        была известна и ещё активна."""
        with self._lock:
            entry = self._entries.get(str(key or ""))
            if entry is None or not entry.snapshot.is_active:
                return False
            cancel = entry.cancel
        if callable(cancel):
            try:
                cancel()
            except Exception:
                return False
        return True

    def cancel_owner(self, owner):
        wanted = str(owner or "")
        keys = [
            snapshot.key
            for snapshot in self.active()
            if snapshot.owner == wanted
        ]
        cancelled = 0
        for key in keys:
            if self.cancel(key):
                cancelled += 1
        return cancelled

    def remove(self, key):
        with self._lock:
            return self._entries.pop(str(key or ""), None) is not None

    def clear_finished(self):
        with self._lock:
            protected = self._protected_keys_locked()
            keys = [
                key
                for key, entry in self._entries.items()
                if entry.snapshot.is_finished
                and key not in protected
            ]
            for key in keys:
                self._entries.pop(key, None)
                if self._known_states.get(key) == DONE:
                    self._known_states.pop(key, None)
            return len(keys)

    # -------------------------------------------------- служебное
    @staticmethod
    def _normalize(snapshot):
        if not isinstance(snapshot, OperationSnapshot):
            raise TypeError("OperationRegistry принимает OperationSnapshot")
        if not snapshot.key:
            raise ValueError("operation key не может быть пустым")
        if snapshot.state not in ALL_STATES:
            raise ValueError(
                "неизвестное состояние операции: %s" % snapshot.state
            )
        return snapshot

    def _trim_locked(self):
        protected = self._protected_keys_locked()
        finished = [
            (index, entry.snapshot)
            for index, entry in enumerate(self._entries.values())
            if entry.snapshot.is_finished
            and entry.snapshot.key not in protected
        ]
        if len(finished) <= self._max_finished:
            return
        finished.sort(
            key=lambda item: (
                item[1].finished_at or item[1].started_at or 0.0,
                item[0],
            ),
            reverse=True,
        )
        for _index, snapshot in finished[self._max_finished :]:
            self._entries.pop(snapshot.key, None)
