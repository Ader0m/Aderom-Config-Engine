# -*- coding: utf-8 -*-
"""Общие helpers кэширующих сервисов резолва LTX/XML.

LTX и XML-резолв используют один и тот же :class:`ResolvedCache` и одинаково
объясняют промахи в логах. Здесь собрана эта общая часть; конкретные
ключи и вызовы резолверов остаются в своих сервисах.
"""

from __future__ import annotations


def miss_reason(previous, key, labels) -> str:
    """Человекочитаемая причина промаха кэша для диагностики."""
    if previous is None:
        return "нет предыдущего ключа"

    changed = []
    for index, label in enumerate(labels):
        if index >= len(previous) or index >= len(key):
            changed.append("формат ключа")
            break
        if previous[index] != key[index]:
            changed.append(label)

    return "изменилось: " + ", ".join(changed) if changed else "ключи совпадают"
