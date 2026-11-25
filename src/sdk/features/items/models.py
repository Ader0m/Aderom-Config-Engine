# -*- coding: utf-8 -*-
"""DTO предметов: каталог, записи, секции, иконки."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class ItemField:
    """Одно поле игрового предмета."""

    key: str
    value: str
    line_number: int = 0

@dataclass(frozen=True)
class LocalizationString:
    """Победившее объявление строки локализации.

    ``path`` — XML-документ (включая ``#include``-фрагмент), в котором
    записан текст; ``line`` — строка в этом документе.
    """

    key: str
    text: str = ""
    path: Optional[Path] = None
    line: int = 0
    # Исходная строка ``<text>`` с ``\\n`` и ``%c[...]``: её получает
    # редактор карточки, чтобы не терять оформление движка.
    raw_text: str = ""

@dataclass(frozen=True)
class ItemEntry:
    """Предмет из корневого файла предметов (``items.ltx``).

    ``icon_path`` — найденный в VFS атлас ``.dds`` (``None``, если
    текстура не найдена). ``icon_x``/``icon_y``/``icon_width``/
    ``icon_height`` — координаты и размер в ячейках инвентаря по 50 px.
    """

    section: str
    fields: tuple[ItemField, ...] = field(default_factory=tuple)
    icon_texture: str = ""
    icon_path: Optional[Path] = None
    icon_x: int = 0
    icon_y: int = 0
    icon_width: int = 1
    icon_height: int = 1
    line_number: int = 0
    # Готовый текст из configs/text/<lang>, а не ключ локализации.
    name_text: str = ""
    description_text: str = ""
    # Ключи inv_name/description и XML-объявления для их правки.
    name_key: str = ""
    description_key: str = ""
    name_source: Optional[LocalizationString] = None
    description_source: Optional[LocalizationString] = None
    # XML-объявления текстовых полей: ``inv_name``, ``inv_name_short``,
    # ``description``. Ключ — имя поля, значение — источник строки.
    text_sources: dict = field(default_factory=dict)

    def field(self, name: str, default: Optional[str] = None) -> Optional[str]:
        """Значение поля без учёта регистра ключа."""
        wanted = str(name).lower()
        for item in self.fields:
            if item.key.lower() == wanted:
                return item.value
        return default

    @property
    def icon_rect(self) -> tuple[int, int, int, int]:
        """Прямоугольник иконки в пикселях атласа."""
        return (
            self.icon_x * 50,
            self.icon_y * 50,
            self.icon_width * 50,
            self.icon_height * 50,
        )

@dataclass(frozen=True)
class ItemSection:
    """Секция файла предметов: имя, поля и родители.

    ``fields`` — ключи, объявленные в секции; по ним создаётся новый
    предмет с пустыми значениями. ``parents`` — токены наследования
    из заголовка ``[section]:parent1,parent2``.
    """

    name: str
    fields: tuple[str, ...] = field(default_factory=tuple)
    parents: tuple[str, ...] = field(default_factory=tuple)

@dataclass(frozen=True)
class ItemCatalog:
    """Отсортированный список предметов и файл, из которого он собран."""

    source_path: Optional[Path] = None
    items: tuple[ItemEntry, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    # Идентификатор снимка каталога для кэша иконок.
    cache_key: str = ""
    # Все секции документа, включая служебные родительские секции.
    sections: tuple[ItemSection, ...] = field(default_factory=tuple)
    # Родители, встречающиеся в заголовках исходных LTX.
    parent_names: tuple[str, ...] = field(default_factory=tuple)

@dataclass(frozen=True)
class ItemIcon:
    """Декодированная иконка предмета в формате RGBA."""

    section: str
    width: int
    height: int
    rgba: bytes = b""

@dataclass(frozen=True)
class TextureRegion:
    """Прямоугольник DDS в формате RGBA (4 байта на пиксель)."""

    width: int
    height: int
    rgba: bytes = b""

@dataclass(frozen=True)
class ItemCardRequest:
    """Запрос на открытие карточки предмета из resolved-документа."""

    tab_id: str
    section: str
    line_number: int = 0


@dataclass(frozen=True)
class ItemCreationPlan:
    """Готовые тексты нового предмета для записи в проект."""

    section: str
    tag: str
    item_text: str
    xml_text: str
    name_key: str = ""
    description_key: str = ""
