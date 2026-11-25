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
    cancelled: bool = False


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
        cache_dir=None,
        resolved_max_entries=None,
        resolved_max_chars=None,
        resolved_max_disk_bytes=None,
    ):
        self._lock = threading.RLock()
        self._format_version = int(format_version)
        self._memory = memory_cache or ObjectCache(max_entries=64)
        if resolved_cache is None:
            resolved_cache = ResolvedCache(
                cache_dir=cache_dir,
                max_entries=resolved_max_entries,
                max_chars=resolved_max_chars,
                max_disk_bytes=resolved_max_disk_bytes,
                format_version=self._format_version,
            )
        self._resolved = resolved_cache
        # Семантическая модель ссылается на объекты Resolver4 и не
        # сериализуется. Держим только несколько корней в памяти.
        self._semantic = ObjectCache(max_entries=4)
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
        """Назначить бэкенд пространству имён явно.

        Backend должен поддерживать ``get``/``set``/``delete``/``clear``,
        ``clear_disk`` и ``stats``: ``CacheService`` делегирует ему
        глобальные операции очистки и диагностику.
        """
        with self._lock:
            self._backends[str(namespace)] = backend

    def _backend(self, namespace):
        with self._lock:
            backend = self._backends.get(namespace)
        if backend is not None:
            return backend
        if namespace == "resolve.semantic":
            return self._semantic
        if namespace in ("resolve.text", "xml"):
            return self._resolved
        return self._memory

    def backend_stats(self):
        with self._lock:
            registered = dict(self._backends)
        stats = {
            "memory": self._memory.stats(),
            "resolved": self._resolved.stats(),
            "semantic": self._semantic.stats(),
        }
        for namespace, backend in registered.items():
            stats[namespace] = backend.stats()
        return stats

    # -------------------------------------------------- публичный API
    def get_or_compute(
        self,
        namespace,
        key,
        compute,
        deps=(),
        snapshot_id="",
        cacheable=None,
    ):
        """Вернуть значение или вычислить его ровно один раз.

        ``key`` — логический ключ без namespace и снимка. Снимок хранится
        рядом с записью и проверяется при чтении; старые записи не
        возвращаются даже при совпадении логического ключа.

        ``cacheable`` — необязательный предикат: если он вернул ``False``,
        результат возвращается вызывающему, но не сохраняется в backend.
        Нужен для fallback/partial-результатов, которые нельзя считать
        полноценным снимком.
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
            can_store = value is not None and not flight.cancelled
            if can_store and cacheable is not None:
                can_store = bool(cacheable(value))
            if can_store:
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

    def clear_namespace(self, namespace):
        """Очистить память backend'а namespace; диск сохраняется.

        Нужен владельцам namespace'ов для их локального ``clear_memory``,
        не затрагивающего остальные кэши общего ``CacheService``.
        Поддерживает зарегистрированные backend'ы и встроенные
        ``resolve.text``/``xml``/``resolve.semantic``. Метаданные снимаются
        без ``backend.delete``: иначе вместе с памятью удалялась бы и
        дисковая запись. In-flight вычисления этого namespace помечаются
        отменёнными: ожидающие получают ошибку, а owner не записывает
        результат обратно в кэш.
        """
        namespace = str(namespace)
        with self._lock:
            backend = self._backends.get(namespace)
            if backend is None:
                if namespace == "resolve.semantic":
                    backend = self._semantic
                elif namespace in ("resolve.text", "xml"):
                    backend = self._resolved
                else:
                    raise KeyError(
                        "namespace %r не зарегистрирован" % namespace
                    )
            keys = [
                entry_key
                for entry_key in self._meta
                if entry_key[0] == namespace
            ]
            for entry_key, flight in self._inflight.items():
                if entry_key[0] != namespace:
                    continue
                flight.cancelled = True
                flight.error = RuntimeError(
                    "namespace %r очищен во время вычисления"
                    % namespace
                )
                flight.event.set()
        for entry_key in keys:
            with self._lock:
                self._drop_metadata_locked(entry_key)
        backend.clear()

    def clear(self):
        with self._lock:
            self._meta.clear()
            self._snapshot_keys.clear()
            self._dep_keys.clear()
            for flight in self._inflight.values():
                flight.cancelled = True
                flight.error = RuntimeError("кэш очищен во время вычисления")
                flight.event.set()
            self._inflight.clear()
            backends = list({
                id(backend): backend
                for backend in self._backends.values()
            }.values())
        self._memory.clear()
        self._resolved.clear()
        self._semantic.clear()
        for backend in backends:
            backend.clear()

    def clear_disk(self):
        self._resolved.clear_disk()
        with self._lock:
            backends = list({
                id(backend): backend
                for backend in self._backends.values()
                if backend is not self._resolved
            }.values())
        for backend in backends:
            backend.clear_disk()

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
