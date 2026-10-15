# -*- coding: utf-8 -*-
"""Версия SDK и проверка совместимости."""

SDK_VERSION = "0.1.0"


def parse_version(v):
    parts = []
    for x in str(v).split("."):
        try:
            parts.append(int(x))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_compatible(sdk_min):
    """Модуль совместим, если требуемая им версия SDK не выше текущей."""
    return parse_version(sdk_min) <= parse_version(SDK_VERSION)