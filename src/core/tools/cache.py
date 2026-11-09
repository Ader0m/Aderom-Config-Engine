# -*- coding: utf-8 -*-
"""Кэши ядра: память + диск.

Здесь собраны все кэши, которые раньше были разложены по трём модулям:

- :class:`DiskBackedLRUCache` — общий потокобезопасный LRU-кэш с
  необязательным дисковым слоем ``gzip+pickle``;
- :class:`ResolvedCache` — кэш результатов резолва (текст + источники);
- :class:`ObjectCache` — кэш произвольных picklable-объектов;
- :class:`FileCacheSystem` — простой in-memory кэш для дерева и запросов.

Дисковый формат намеренно сохранён: ключ сериализуется pickle, имя файла —
SHA1, полезная нагрузка — ``{"key": key, "value": value}`` в gzip. Поэтому
уже существующий кэш пользователей продолжает читаться.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import pickle
import threading
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


def invalidate_forest_signatures(forest):
    """Оставить совместимый сброс на случай внешних вызовов."""
    try:
        forest._resolved_signature_memo = {}
    except Exception:
        pass


def path_key(path) -> str:
    """Стабильная идентичность пути: абсолютный, нормализованный, без регистра."""
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path))))


def file_subtree_signature(forest, root_path) -> str:
    """SHA1 подписи файла и всех его include/DLTX-зависимостей.

    Считается по поддереву конкретного файла, а не по всему лесу:
    проверка одного файла не должна делать stat всех 4000+ LTX.
    mtime/size/ctime читаются заново, поэтому внешние правки видны.
    """
    children_map = getattr(forest, "display_children", {}) or {}

    def calc(node, stack):
        key = os.path.normcase(os.path.abspath(str(node)))
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

        return digest.hexdigest()

    return calc(root_path, frozenset())


# ---------------------------------------------------------------- база


class DiskBackedLRUCache:
    """Потокобезопасный LRU-кэш с необязательным дисковым слоем.

    ``size_fn`` задаёт «вес» значения для ограничения ``max_units``.
    Если ``size_fn`` не передан, вес считается нулевым и работает только
    ограничение по числу записей.
    """

    def __init__(
        self,
        cache_dir=None,
        max_entries=None,
        max_units=None,
        size_fn=None,
    ):
        self._data = OrderedDict()
        self._lock = threading.RLock()
        self._max_entries = max_entries
        self._max_units = max_units
        self._size_fn = size_fn
        self._units = 0

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
            self._units -= self._value_size(old)

        self._data[key] = value
        self._units += self._value_size(value)

        if self._max_entries is not None:
            while len(self._data) > self._max_entries:
                self._evict_one()

        if self._max_units is not None:
            while self._units > self._max_units and len(self._data) > 1:
                self._evict_one()

    def _evict_one(self):
        _key, value = self._data.popitem(last=False)
        self._units -= self._value_size(value)
        if self._units < 0:
            self._units = 0

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
            self._units = 0
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

    def stats(self):
        with self._lock:
            return {
                "entries": len(self._data),
                "units": self._units,
                "hits": self.hits,
                "misses": self.misses,
                "disk_hits": self.disk_hits,
                "disk_writes": self.disk_writes,
            }


# ---------------------------------------------------------------- специализации


class ResolvedCache(DiskBackedLRUCache):
    """Потокобезопасный кэш результатов резолва (память + диск)."""

    def __init__(self, max_entries=None, max_chars=None, cache_dir=None):
        super().__init__(
            cache_dir=cache_dir,
            max_entries=max_entries,
            max_units=max_chars,
            size_fn=self._text_size,
        )

    @staticmethod
    def _text_size(value):
        text = value[0] if isinstance(value, tuple) else value
        return len(text) if isinstance(text, str) else 0

    def stats(self):
        stats = super().stats()
        stats["chars"] = stats.pop("units")
        return stats


class ObjectCache(DiskBackedLRUCache):
    """LRU-кэш произвольных picklable-объектов."""

    def __init__(
        self,
        cache_dir=None,
        max_entries=None,
        max_bytes=None,
        size_fn=None,
    ):
        super().__init__(
            cache_dir=cache_dir,
            max_entries=max_entries,
            max_units=max_bytes,
            size_fn=size_fn,
        )

    def clear(self):
        """Очистить память. Диск и статистика сохраняются."""
        with self._lock:
            self._data.clear()
            self._units = 0

    def stats(self):
        stats = super().stats()
        stats["bytes"] = stats.pop("units")
        return stats


# ---------------------------------------------------------------- простой кэш


class FileCacheSystem:
    """Центральный in-memory кэш тяжёлых данных.

    В него можно класть дерево, результаты запросов дерева, результаты
    резолвера, граф связей и превью патчей.
    """

    def __init__(self):
        self._data = {}
        self._snapshot_id = None

    def set_snapshot(self, snapshot_id):
        """Если снимок файловой системы изменился, кэш недействителен."""
        if snapshot_id != self._snapshot_id:
            self.clear()
            self._snapshot_id = snapshot_id

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value

    def invalidate(self, prefix=None):
        """Инвалидация ключей.

        Если ``prefix`` равен None, очищается весь кэш.
        Если ``prefix`` передан, удаляются ключи, начинающиеся с него.
        """
        if prefix is None:
            self.clear()
            return

        for key in list(self._data.keys()):
            if isinstance(key, tuple) and key[:len(prefix)] == prefix:
                del self._data[key]

    def clear(self):
        self._data.clear()
