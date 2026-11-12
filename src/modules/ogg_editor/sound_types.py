# -*- coding: utf-8 -*-
"""Справочник игровых типов звука X-Ray.

Значения взяты из ``xrServerEntities/ai_sounds.h``. Это именно битовые
маски, а не «магические числа»: редактор хранит точное число, а
справочник только помогает выбрать его человеку.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class SoundTypeEntry:
    value: int
    code: str
    label: str
    group: str = ""

    @property
    def display(self) -> str:
        prefix = ("%s: " % self.group) if self.group else ""
        return "%s%s — 0x%08X" % (prefix, self.label, self.value)

    @property
    def tooltip(self) -> str:
        return "%s = 0x%08X (%d)" % (self.code, self.value, self.value)


def _entry(value, code, label, group=""):
    return SoundTypeEntry(
        value=int(value),
        code=code,
        label=label,
        group=group,
    )


SOUND_TYPES: Tuple[SoundTypeEntry, ...] = (
    _entry(0x00000000, "SOUND_TYPE_NO_SOUND", "undefined", ""),
    _entry(0x80000000, "SOUND_TYPE_WEAPON", "Оружие", "Маска"),
    _entry(0x40000000, "SOUND_TYPE_ITEM", "Предмет", "Маска"),
    _entry(0x20000000, "SOUND_TYPE_MONSTER", "Монстр", "Маска"),
    _entry(0x10000000, "SOUND_TYPE_ANOMALY", "Аномалия", "Маска"),
    _entry(0x08000000, "SOUND_TYPE_WORLD", "Мир", "Маска"),

    _entry(0x04000000, "SOUND_TYPE_PICKING_UP", "Подбор", "Бит"),
    _entry(0x02000000, "SOUND_TYPE_DROPPING", "Выбрасывание", "Бит"),
    _entry(0x01000000, "SOUND_TYPE_HIDING", "Скрытие", "Бит"),
    _entry(0x00800000, "SOUND_TYPE_TAKING", "Взятие", "Бит"),
    _entry(0x00400000, "SOUND_TYPE_USING", "Использование", "Бит"),
    _entry(0x00200000, "SOUND_TYPE_SHOOTING", "Выстрел", "Бит"),
    _entry(0x00100000, "SOUND_TYPE_EMPTY_CLICKING", "Щелчок без патронов", "Бит"),
    _entry(0x00080000, "SOUND_TYPE_BULLET_HIT", "Попадание пули", "Бит"),
    _entry(0x00040000, "SOUND_TYPE_RECHARGING", "Перезарядка", "Бит"),
    _entry(0x00020000, "SOUND_TYPE_DYING", "Смерть", "Бит"),
    _entry(0x00010000, "SOUND_TYPE_INJURING", "Ранение", "Бит"),
    _entry(0x00008000, "SOUND_TYPE_STEP", "Шаг", "Бит"),
    _entry(0x00004000, "SOUND_TYPE_TALKING", "Разговор", "Бит"),
    _entry(0x00002000, "SOUND_TYPE_ATTACKING", "Атака", "Бит"),
    _entry(0x00001000, "SOUND_TYPE_EATING", "Еда", "Бит"),
    _entry(0x00000800, "SOUND_TYPE_IDLE", "Ожидание", "Бит"),
    _entry(0x00000400, "SOUND_TYPE_OBJECT_BREAKING", "Разрушение объекта", "Бит"),
    _entry(0x00000200, "SOUND_TYPE_OBJECT_COLLIDING", "Столкновение объекта", "Бит"),
    _entry(0x00000100, "SOUND_TYPE_OBJECT_EXPLODING", "Взрыв объекта", "Бит"),
    _entry(0x00000080, "SOUND_TYPE_AMBIENT", "Окружение", "Бит"),

    _entry(0x44000000, "SOUND_TYPE_ITEM_PICKING_UP", "Подбор", "Предмет"),
    _entry(0x42000000, "SOUND_TYPE_ITEM_DROPPING", "Выбрасывание", "Предмет"),
    _entry(0x41000000, "SOUND_TYPE_ITEM_HIDING", "Скрытие", "Предмет"),
    _entry(0x40800000, "SOUND_TYPE_ITEM_TAKING", "Взятие", "Предмет"),
    _entry(0x40400000, "SOUND_TYPE_ITEM_USING", "Использование", "Предмет"),

    _entry(0x80200000, "SOUND_TYPE_WEAPON_SHOOTING", "Выстрел", "Оружие"),
    _entry(0x80100000, "SOUND_TYPE_WEAPON_EMPTY_CLICKING", "Щелчок без патронов", "Оружие"),
    _entry(0x80080000, "SOUND_TYPE_WEAPON_BULLET_HIT", "Попадание пули", "Оружие"),
    _entry(0x80040000, "SOUND_TYPE_WEAPON_RECHARGING", "Перезарядка", "Оружие"),

    _entry(0x20020000, "SOUND_TYPE_MONSTER_DYING", "Смерть", "Монстр"),
    _entry(0x20010000, "SOUND_TYPE_MONSTER_INJURING", "Ранение", "Монстр"),
    _entry(0x20008000, "SOUND_TYPE_MONSTER_STEP", "Шаг", "Монстр"),
    _entry(0x20004000, "SOUND_TYPE_MONSTER_TALKING", "Разговор", "Монстр"),
    _entry(0x20002000, "SOUND_TYPE_MONSTER_ATTACKING", "Атака", "Монстр"),
    _entry(0x20001000, "SOUND_TYPE_MONSTER_EATING", "Еда", "Монстр"),

    _entry(0x10000800, "SOUND_TYPE_ANOMALY_IDLE", "Ожидание", "Аномалия"),

    _entry(0x08000400, "SOUND_TYPE_WORLD_OBJECT_BREAKING", "Разрушение объекта", "Мир"),
    _entry(0x08000200, "SOUND_TYPE_WORLD_OBJECT_COLLIDING", "Столкновение объекта", "Мир"),
    _entry(0x08000100, "SOUND_TYPE_WORLD_OBJECT_EXPLODING", "Взрыв объекта", "Мир"),
    _entry(0x08000080, "SOUND_TYPE_WORLD_AMBIENT", "Окружение", "Мир"),
)

# 21 канонических типов из anomaly_type_token (xrGame/ai_sounds.cpp):
# undefined + 20 готовых комбинированных типов.
BASIC_SOUND_TYPES: Tuple[SoundTypeEntry, ...] = tuple(
    entry
    for entry in SOUND_TYPES
    if entry.value == 0
    or entry.group in ("Предмет", "Оружие", "Монстр", "Аномалия", "Мир")
)

# Остальные значения enum ESoundTypes: базовые маски и отдельные биты.
EXTENDED_SOUND_TYPES: Tuple[SoundTypeEntry, ...] = tuple(
    entry for entry in SOUND_TYPES if entry not in BASIC_SOUND_TYPES
)

_BASIC_VALUES = {entry.value for entry in BASIC_SOUND_TYPES}
_BY_VALUE = {entry.value: entry for entry in SOUND_TYPES}


def find_sound_type(value) -> Optional[SoundTypeEntry]:
    try:
        return _BY_VALUE.get(int(value))
    except (TypeError, ValueError):
        return None


def sound_type_display(value) -> str:
    entry = find_sound_type(value)
    if entry is not None:
        return entry.display
    try:
        return "Неизвестный тип — 0x%08X" % int(value)
    except (TypeError, ValueError):
        return "Неизвестный тип"


def sound_type_short(value) -> str:
    """Компактная подпись для таблицы дерева."""
    entry = find_sound_type(value)
    if entry is not None:
        if entry.group:
            return "%s: %s" % (entry.group, entry.label)
        return entry.label
    try:
        return "0x%08X" % int(value)
    except (TypeError, ValueError):
        return "?"


def sound_type_tooltip(value) -> str:
    entry = find_sound_type(value)
    if entry is not None:
        return entry.tooltip
    try:
        return "Нет в справочнике; точное значение: %d" % int(value)
    except (TypeError, ValueError):
        return "Нет в справочнике"


def is_basic_value(value) -> bool:
    try:
        return int(value) in _BASIC_VALUES
    except (TypeError, ValueError):
        return False


def entries_for_combo(extended=False):
    return EXTENDED_SOUND_TYPES if extended else BASIC_SOUND_TYPES
