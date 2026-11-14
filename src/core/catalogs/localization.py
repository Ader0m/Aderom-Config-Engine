# -*- coding: utf-8 -*-
"""Индекс ключей локализации для подсказок.

Сортированный снимок строится поверх :class:`LocalizationService`: сам
сервис отвечает за include, языки и overlay, а индекс — только за
быстрый prefix-поиск.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Dict, Mapping, Tuple

CATALOG_VERSION = "1"


@dataclass(frozen=True)
class LocalizationSet:
    keys: Tuple[str, ...]
    lower_keys: Tuple[str, ...]
    by_lower: Mapping[str, str]
    strings: Mapping[str, str]
    entries: Mapping[str, object]
    signature: str = ""

    def text(self, key) -> str:
        value = self.strings.get(str(key or ""), "")
        return "" if value is None else str(value)

    def entry(self, key):
        return self.entries.get(str(key or ""))

    def prefix(self, prefix, limit=100) -> Tuple[str, ...]:
        prefix = str(prefix or "")
        limit = int(limit)
        if limit <= 0:
            return ()

        result = []
        seen = set()
        if prefix:
            start = bisect.bisect_left(self.keys, prefix)
            for index in range(start, len(self.keys)):
                key = self.keys[index]
                if not key.startswith(prefix):
                    break
                result.append(key)
                seen.add(key)
                if len(result) >= limit:
                    return tuple(result)
        else:
            for key in self.keys[:limit]:
                result.append(key)
                seen.add(key)
            return tuple(result)

        # Fallback без учёта регистра: ключи X-Ray обычно в нижнем
        # регистре, но пользователь может набрать иначе.
        lowered = prefix.lower()
        start = bisect.bisect_left(self.lower_keys, lowered)
        for index in range(start, len(self.lower_keys)):
            low = self.lower_keys[index]
            if not low.startswith(lowered):
                break
            key = self.by_lower.get(low)
            if key is None or key in seen:
                continue
            result.append(key)
            seen.add(key)
            if len(result) >= limit:
                break
        return tuple(result)

    def __len__(self):
        return len(self.keys)


EMPTY_LOCALIZATION = LocalizationSet(
    keys=(),
    lower_keys=(),
    by_lower={},
    strings={},
    entries={},
)


class LocalizationIndex:
    """Строит ``LocalizationSet`` из таблицы ``LocalizationService``."""

    def __init__(self):
        self._key = None
        self._value = EMPTY_LOCALIZATION

    def build(self, table, signature="") -> LocalizationSet:
        if table is None:
            return EMPTY_LOCALIZATION
        strings = dict(getattr(table, "strings", {}) or {})
        entries = dict(getattr(table, "entries", {}) or {})
        key = (
            CATALOG_VERSION,
            str(signature or getattr(table, "signature", "") or ""),
            len(strings),
        )
        if key == self._key:
            return self._value

        keys = tuple(sorted(str(key) for key in strings))
        by_lower = {}
        for key_name in keys:
            by_lower.setdefault(key_name.lower(), key_name)
        lower_keys = tuple(sorted(by_lower))
        value = LocalizationSet(
            keys=keys,
            lower_keys=lower_keys,
            by_lower=by_lower,
            strings=strings,
            entries=entries,
            signature=str(key),
        )
        self._key = key
        self._value = value
        return value

    def clear(self):
        self._key = None
        self._value = EMPTY_LOCALIZATION
