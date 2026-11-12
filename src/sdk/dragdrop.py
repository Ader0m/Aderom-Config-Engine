# -*- coding: utf-8 -*-
"""Общий контракт drag-and-drop между модулями.

Сейчас используется для переноса предмета из окна просмотра предметов
в другие модули: передаётся имя секции и все её поля.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable, Optional

ITEM_SECTION_MIME = "application/x-ace-item-section+json"


@dataclass(frozen=True)
class ItemSectionData:
    """Предмет, переносимый между окнами модулей."""

    section: str
    fields: tuple[tuple[str, str], ...] = ()

    def field_values(self, key: str):
        wanted = str(key).lower()
        return [
            value
            for name, value in self.fields
            if str(name).lower() == wanted
        ]


def _field_pair(field):
    if hasattr(field, "key"):
        return str(getattr(field, "key", "") or ""), str(
            getattr(field, "value", "") or ""
        )
    if isinstance(field, (tuple, list)) and len(field) >= 2:
        return str(field[0]), str(field[1])
    return "", ""


def encode_item_section(section: str, fields: Iterable) -> bytes:
    payload = {
        "section": str(section or ""),
        "fields": [
            {"key": key, "value": value}
            for key, value in (_field_pair(item) for item in fields)
            if key
        ],
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def decode_item_section(data: bytes) -> Optional[ItemSectionData]:
    if not data:
        return None
    try:
        payload = json.loads(bytes(data).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    section = str(payload.get("section") or "")
    if not section:
        return None

    fields = []
    for item in payload.get("fields") or ():
        if isinstance(item, dict):
            key = str(item.get("key") or "")
            value = str(item.get("value") or "")
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            key = str(item[0])
            value = str(item[1])
        else:
            continue
        if key:
            fields.append((key, value))

    return ItemSectionData(section=section, fields=tuple(fields))
