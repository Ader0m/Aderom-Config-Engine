# -*- coding: utf-8 -*-
"""Кэш результатов резолва LTX.

Два уровня:
- память (LRU/без лимита);
- диск (gzip+pickle) — переживает перезапуск приложения.

Ключ включает подпись поддерева файла: mtime+size самого файла, всех его
include/DLTX-зависимостей и самого Resolver4.py. Если метки не изменились —
это cache hit. При изменении любого зависимого файла ключ меняется, и
резолв выполняется заново.

Кэш потокобезопасен: фоновую предсборку и открытие файла пользователем
можно выполнять параллельно.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import pickle
import threading
import time
from collections import OrderedDict
from pathlib import Path


# ---------------------------------------------------------------- подписи

def file_stamp(path) -> str:
    """Метка файла: mtime_ns + size + ctime_ns. Без чтения содержимого."""
    try:
        st = os.stat(str(path))
        return "%d:%d:%d" % (
            st.st_mtime_ns,
            st.st_size,
            st.st_ctime_ns,
        )
    except OSError:
        return "missing"


def resolver_signature(resolver_script) -> str:
    """Подпись внешнего Resolver4.py."""
    if resolver_script is None:
        return ""
    return "%s:%s" % (
        os.path.normcase(os.path.abspath(str(resolver_script))),
        file_stamp(resolver_script),
    )


def file_subtree_signature(forest, root_path) -> str:
    """SHA1 подписи файла и всех его include/DLTX-зависимостей.

    ``forest.display_children`` уже содержит include-детей и
    присоединённые mod_*.ltx. Для файла вне дерева остаётся только
    собственная метка.
    """
    memo = getattr(forest, "_resolved_signature_memo", None)
    lock = getattr(forest, "_resolved_signature_lock", None)

    if memo is None:
        memo = {}
        lock = threading.RLock()
        try:
            forest._resolved_signature_memo = memo
            forest._resolved_signature_lock = lock
        except Exception:
            pass

    if lock is None:
        lock = threading.RLock()

    children_map = getattr(forest, "display_children", {}) or {}

    with lock:
        def calc(node, stack):
            key = os.path.normcase(os.path.abspath(str(node)))
            if key in memo:
                return memo[key]
            if key in stack:
                return "cycle"

            digest = hashlib.sha1()
            digest.update(key.encode("utf-8", "replace"))
            digest.update(b"\0")
            digest.update(file_stamp(node).encode("ascii"))

            children = children_map.get(Path(node), []) or []
            for child in children:
                digest.update(b"\1")
                digest.update(
                    calc(child, stack | frozenset((key,))).encode("ascii")
                )

            value = digest.hexdigest()
            memo[key] = value
            return value

        return calc(root_path, frozenset())


# ---------------------------------------------------------------- кэш

class ResolvedCache:
    """Потокобезопасный кэш результатов резолва (память + диск)."""

    def __init__(self, max_entries=None, max_chars=None, cache_dir=None):
        self._data = OrderedDict()
        self._lock = threading.RLock()
        self._max_entries = max_entries
        self._max_chars = max_chars
        self._chars = 0

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
    def _put_memory(self, key, value):
        text = value[0] if isinstance(value, tuple) else value
        size = len(text) if isinstance(text, str) else 0

        old = self._data.pop(key, None)
        if old is not None:
            old_text = old[0] if isinstance(old, tuple) else old
            self._chars -= len(old_text) if isinstance(old_text, str) else 0

        self._data[key] = value
        self._chars += size

        if self._max_entries is not None:
            while len(self._data) > self._max_entries:
                self._evict_one()

        if self._max_chars is not None:
            while self._chars > self._max_chars and len(self._data) > 1:
                self._evict_one()

    def _evict_one(self):
        _key, value = self._data.popitem(last=False)
        text = value[0] if isinstance(value, tuple) else value
        if isinstance(text, str):
            self._chars -= len(text)

    def has(self, key):
        """Быстрая проверка наличия без загрузки текста с диска."""
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

        # Чтение с диска — вне блокировки памяти: не блокируем
        # пользовательский cache hit во время фоновой сборки.
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

        # Диск пишем вне блокировки памяти: большой system.ltx не должен
        # задерживать пользовательский cache hit.
        self._save_disk(key, value)

    def clear(self):
        """Очистить память. Дисковый кэш сохраняется."""
        with self._lock:
            self._data.clear()
            self._chars = 0
            self.hits = 0
            self.misses = 0

    def clear_disk(self):
        """Удалить дисковый кэш (например, для диагностики)."""
        with self._lock:
            if self._cache_dir is None:
                return
            for path in self._cache_dir.glob("*.pkl.gz"):
                try:
                    path.unlink()
                except OSError:
                    pass
            self.disk_hits = 0
            self.disk_writes = 0

    def prune_disk(self, max_bytes=None, max_age_days=None):
        """Удалить старые/лишние файлы дискового кэша."""
        if self._cache_dir is None:
            return

        with self._lock:
            files = []
            total = 0
            for path in self._cache_dir.glob("*.pkl.gz"):
                try:
                    st = path.stat()
                except OSError:
                    continue
                files.append((st.st_mtime, st.st_size, path))
                total += st.st_size

            files.sort()

            if max_age_days is not None:
                cutoff = time.time() - max_age_days * 24 * 3600
                for mtime, size, path in list(files):
                    if mtime < cutoff:
                        try:
                            path.unlink()
                            total -= size
                            files.remove((mtime, size, path))
                        except OSError:
                            pass

            if max_bytes is not None:
                for _mtime, size, path in files:
                    if total <= max_bytes:
                        break
                    try:
                        path.unlink()
                        total -= size
                    except OSError:
                        pass

    def stats(self):
        with self._lock:
            return {
                "entries": len(self._data),
                "chars": self._chars,
                "hits": self.hits,
                "misses": self.misses,
                "disk_hits": self.disk_hits,
                "disk_writes": self.disk_writes,
            }
