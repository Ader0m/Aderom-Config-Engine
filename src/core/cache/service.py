# -*- coding: utf-8 -*-
"""CacheService — единая точка доступа к кэшам ядра.

Пространства имён разделяют данные с разной ценой и политикой вытеснения.
Бэкенды (`ObjectCache`, `ResolvedCache`) остаются хранилищами, а
`CacheService` отвечает за ключ, снимок, зависимости, single-flight и
статистику.
"""

from __future__ import annotations

import threading
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field

from core.tools.cache import (
    CACHE_FORMAT_VERSION,
    ObjectCache,
    ResolvedCache,
)
from core.tools.paths import path_key


@dataclass
class _Entry:
    namespace: str
    key: object
    snapshot_id: str
    deps: tuple = ()


@dataclass
class _Flight:
    event: threading.Event = field(default_factory=threading.Event)
    value: object = None
    error: BaseException | None = None


class CacheService:
    """Кэш с пространствами имён, снимком и дедупликацией вычислений."""

    # Метаданные ограничены независимо от размера backend'ов. Потеря
    # метаданных безопасна: ключ уже содержит namespace/snapshot/данные.
    _MAX_META_ENTRIES = 512

    def __init__(
        self,
        memory_cache=None,
        resolved_cache=None,
        format_version=CACHE_FORMAT_VERSION,
    ):
        self._lock = threading.RLock()
        self._format_version = int(format_version)
        self._memory = memory_cache or ObjectCache(max_entries=64)
        self._resolved = resolved_cache or ResolvedCache(
            format_version=self._format_version,
        )
        self._backends = {}
        self._meta = OrderedDict()
        self._snapshot_keys = defaultdict(set)
        self._dep_keys = defaultdict(set)
        self._inflight = {}
        self._stats = defaultdict(
            lambda: {
                "hits": 0,
                "misses": 0,
                "computes": 0,
                "waits": 0,
                "errors": 0,
            }
        )

    # -------------------------------------------------- backend'ы
    def register_backend(self, namespace, backend):
        """Назначить бэкенд пространству имён явно."""
        with self._lock:
            self._backends[str(namespace)] = backend

    def _backend(self, namespace):
        with self._lock:
            backend = self._backends.get(namespace)
        if backend is not None:
            return backend
        if namespace in ("resolve.text", "resolve.semantic", "xml"):
            return self._resolved
        return self._memory

    def backend_stats(self):
        return {
            "memory": self._memory.stats(),
            "resolved": self._resolved.stats(),
        }

    # -------------------------------------------------- публичный API
    def get_or_compute(
        self,
        namespace,
        key,
        compute,
        deps=(),
        snapshot_id="",
    ):
        """Вернуть значение или вычислить его ровно один раз.

        ``key`` — логический ключ без namespace и снимка. Снимок хранится
        рядом с записью и проверяется при чтении; старые записи не
        возвращаются даже при совпадении логического ключа.
        """
        namespace = str(namespace)
        snapshot_id = str(snapshot_id or "")
        entry_key = self._entry_key(namespace, key, snapshot_id)
        deps = tuple(self._dep_key(dep) for dep in (deps or ()))
        backend = self._backend(namespace)

        with self._lock:
            entry = self._meta.get(entry_key)

        if entry is not None:
            if entry.snapshot_id == snapshot_id:
                # Чтение диска не держит общий лок: иначе медленный
                # resolved-кэш блокировал бы дерево и запросы.
                value = backend.get(entry_key)
                if value is not None:
                    with self._lock:
                        if self._meta.get(entry_key) is entry:
                            self._meta.move_to_end(entry_key)
                            self._stats[namespace]["hits"] += 1
                            return value
            with self._lock:
                self._forget_locked(entry_key)
        else:
            # После перезапуска метаданных в памяти нет, но дисковый
            # backend может содержать запись. Ключ уже включает снимок
            # и зависимости, поэтому такая запись безопасна.
            value = backend.get(entry_key)
            if value is not None:
                self._remember(
                    entry_key,
                    _Entry(
                        namespace=namespace,
                        key=key,
                        snapshot_id=snapshot_id,
                        deps=deps,
                    ),
                )
                with self._lock:
                    self._stats[namespace]["hits"] += 1
                return value

        with self._lock:
            flight = self._inflight.get(entry_key)
            if flight is not None:
                self._stats[namespace]["waits"] += 1
                owner = False
            else:
                flight = _Flight()
                self._inflight[entry_key] = flight
                owner = True
                self._stats[namespace]["misses"] += 1

        if not owner:
            flight.event.wait()
            if flight.error is not None:
                raise flight.error
            return flight.value

        try:
            value = compute()
            if value is not None:
                backend.set(entry_key, value)
                self._remember(
                    entry_key,
                    _Entry(
                        namespace=namespace,
                        key=key,
                        snapshot_id=snapshot_id,
                        deps=deps,
                    ),
                )
            flight.value = value
            with self._lock:
                self._stats[namespace]["computes"] += 1
            return value
        except BaseException as exc:
            with self._lock:
                self._stats[namespace]["errors"] += 1
            flight.error = exc
            raise
        finally:
            with self._lock:
                if self._inflight.get(entry_key) is flight:
                    self._inflight.pop(entry_key, None)
            flight.event.set()

    def has(self, namespace, key, snapshot_id=None):
        namespace = str(namespace)
        snapshot_id = "" if snapshot_id is None else str(snapshot_id)
        entry_key = self._entry_key(namespace, key, snapshot_id)
        with self._lock:
            entry = self._meta.get(entry_key)
            if entry is None:
                return self._backend(namespace).has(entry_key)
            if entry.snapshot_id != snapshot_id:
                return False
        return self._backend(namespace).has(entry_key)

    def get(self, namespace, key, snapshot_id=None):
        namespace = str(namespace)
        snapshot_id = "" if snapshot_id is None else str(snapshot_id)
        entry_key = self._entry_key(namespace, key, snapshot_id)
        return self._backend(namespace).get(entry_key)

    def set(self, namespace, key, value, deps=(), snapshot_id=""):
        namespace = str(namespace)
        snapshot_id = str(snapshot_id or "")
        entry_key = self._entry_key(namespace, key, snapshot_id)
        deps = tuple(self._dep_key(dep) for dep in (deps or ()))
        self._backend(namespace).set(entry_key, value)
        self._remember(
            entry_key,
            _Entry(
                namespace=namespace,
                key=key,
                snapshot_id=snapshot_id,
                deps=deps,
            ),
        )

    def discard(self, namespace, key, snapshot_id=""):
        entry_key = self._entry_key(
            str(namespace),
            key,
            str(snapshot_id or ""),
        )
        with self._lock:
            self._forget_locked(entry_key)

    def invalidate_snapshot(self, snapshot_id):
        """Удалить все записи, посчитанные для указанного снимка."""
        snapshot_id = str(snapshot_id or "")
        with self._lock:
            keys = list(self._snapshot_keys.get(snapshot_id, ()))
        for entry_key in keys:
            with self._lock:
                self._forget_locked(entry_key)

    def invalidate_deps(self, deps):
        """Удалить записи, зависящие от указанных путей/меток."""
        keys = set()
        with self._lock:
            for dep in deps or ():
                keys.update(self._dep_keys.get(self._dep_key(dep), ()))
        for entry_key in keys:
            with self._lock:
                self._forget_locked(entry_key)

    def clear(self):
        with self._lock:
            self._meta.clear()
            self._snapshot_keys.clear()
            self._dep_keys.clear()
            for flight in self._inflight.values():
                flight.error = RuntimeError("кэш очищен во время вычисления")
                flight.event.set()
            self._inflight.clear()
        self._memory.clear()
        self._resolved.clear()

    def clear_disk(self):
        self._resolved.clear_disk()

    def stats(self):
        with self._lock:
            namespaces = {
                name: dict(values)
                for name, values in self._stats.items()
            }
        return {
            "namespaces": namespaces,
            "backends": self.backend_stats(),
        }

    # -------------------------------------------------- служебное
    @staticmethod
    def _entry_key(namespace, key, snapshot_id):
        return (namespace, str(snapshot_id or ""), key)

    @staticmethod
    def _dep_key(dep):
        try:
            return path_key(dep)
        except (TypeError, ValueError, OSError):
            return str(dep)

    def _remember(self, entry_key, entry):
        with self._lock:
            self._drop_metadata_locked(entry_key)
            self._meta[entry_key] = entry
            self._snapshot_keys[entry.snapshot_id].add(entry_key)
            for dep in entry.deps:
                self._dep_keys[dep].add(entry_key)
            while len(self._meta) > self._MAX_META_ENTRIES:
                oldest, _entry = next(iter(self._meta.items()))
                self._drop_metadata_locked(oldest)

    def _forget_locked(self, entry_key):
        entry = self._drop_metadata_locked(entry_key)
        if entry is None:
            return
        self._backend(entry.namespace).delete(entry_key)

    def _drop_metadata_locked(self, entry_key):
        entry = self._meta.pop(entry_key, None)
        if entry is None:
            return None
        keys = self._snapshot_keys.get(entry.snapshot_id)
        if keys is not None:
            keys.discard(entry_key)
            if not keys:
                self._snapshot_keys.pop(entry.snapshot_id, None)
        for dep in entry.deps:
            dep_keys = self._dep_keys.get(dep)
            if dep_keys is None:
                continue
            dep_keys.discard(entry_key)
            if not dep_keys:
                self._dep_keys.pop(dep, None)
        return entry
