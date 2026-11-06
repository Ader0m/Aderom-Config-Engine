# -*- coding: utf-8 -*-
"""Небольшой потокобезопасный объектный кэш: память + диск.

Повторяет модель :class:`core.resolved_cache.ResolvedCache`, но не знает,
что именно хранит: каталог предметов, набор иконок или отдельный регион.
Диск — ``gzip+pickle`` с проверкой ключа, поэтому кэш переживает
перезапуск приложения и не отдаёт данные от другого снимка VFS.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import pickle
import threading
from collections import OrderedDict
from pathlib import Path


class ObjectCache:
    """LRU-кэш произвольных picklable-объектов."""

    def __init__(
        self,
        cache_dir=None,
        max_entries=None,
        max_bytes=None,
        size_fn=None,
    ):
        self._data = OrderedDict()
        self._lock = threading.RLock()
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._size_fn = size_fn
        self._bytes = 0

        self._cache_dir = Path(cache_dir) if cache_dir else None
        if self._cache_dir is not None:
            self._cache_dir.mkdir(parents=True, exist_ok=True)

        self.hits = 0
        self.misses = 0
        self.disk_hits = 0
        self.disk_writes = 0

    # -------------------------------------------------- диск
    def _disk_path(self, key):
        blob = pickle.dumps(key, protocol=4)
        digest = hashlib.sha1(blob).hexdigest()
        return self._cache_dir / (digest + ".pkl.gz")

    def _load_disk(self, key):
        if self._cache_dir is None:
            return None

        path = self._disk_path(key)
        if not path.is_file():
            return None

        try:
            with gzip.open(path, "rb", compresslevel=3) as f:
                payload = pickle.load(f)
            if payload.get("key") != key:
                return None
            return payload.get("value")
        except Exception:
            try:
                path.unlink()
            except OSError:
                pass
            return None

    def _save_disk(self, key, value):
        if self._cache_dir is None:
            return

        path = self._disk_path(key)
        tmp = path.with_name(
            path.name
            + ".tmp.%d.%d" % (os.getpid(), threading.get_ident())
        )
        try:
            with gzip.open(tmp, "wb", compresslevel=3) as f:
                pickle.dump(
                    {"key": key, "value": value},
                    f,
                    protocol=4,
                )
            os.replace(str(tmp), str(path))
            with self._lock:
                self.disk_writes += 1
        except Exception:
            try:
                tmp.unlink()
            except OSError:
                pass

    # -------------------------------------------------- память
    def _value_size(self, value):
        if self._size_fn is None:
            return 0
        try:
            return int(self._size_fn(value))
        except Exception:
            return 0

    def _put_memory(self, key, value):
        old = self._data.pop(key, None)
        if old is not None:
            self._bytes -= self._value_size(old)

        self._data[key] = value
        self._bytes += self._value_size(value)

        if self._max_entries is not None:
            while len(self._data) > self._max_entries:
                self._evict_one()

        if self._max_bytes is not None:
            while self._bytes > self._max_bytes and len(self._data) > 1:
                self._evict_one()

    def _evict_one(self):
        _key, value = self._data.popitem(last=False)
        self._bytes -= self._value_size(value)
        if self._bytes < 0:
            self._bytes = 0

    def has(self, key):
        """Проверка наличия без загрузки с диска."""
        with self._lock:
            if key in self._data:
                return True
            if self._cache_dir is None:
                return False
            try:
                return self._disk_path(key).is_file()
            except Exception:
                return False

    def get(self, key, count_miss=True):
        with self._lock:
            item = self._data.get(key)
            if item is not None:
                self._data.move_to_end(key)
                self.hits += 1
                return item

        item = self._load_disk(key)
        if item is not None:
            with self._lock:
                self._put_memory(key, item)
                self.disk_hits += 1
                self.hits += 1
            return item

        with self._lock:
            if count_miss:
                self.misses += 1
        return None

    def set(self, key, value):
        with self._lock:
            self._put_memory(key, value)
        self._save_disk(key, value)

    def clear(self):
        """Очистить память. Диск сохраняется."""
        with self._lock:
            self._data.clear()
            self._bytes = 0

    def clear_disk(self):
        if self._cache_dir is None:
            return
        for path in self._cache_dir.glob("*.pkl.gz"):
            try:
                path.unlink()
            except OSError:
                pass
        with self._lock:
            self.disk_hits = 0
            self.disk_writes = 0

    def stats(self):
        with self._lock:
            return {
                "entries": len(self._data),
                "bytes": self._bytes,
                "hits": self.hits,
                "misses": self.misses,
                "disk_hits": self.disk_hits,
                "disk_writes": self.disk_writes,
            }
