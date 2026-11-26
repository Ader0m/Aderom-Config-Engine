# -*- coding: utf-8 -*-
"""Контракт возможностей предметов (capability ``items.catalog``)."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Protocol  # noqa: F401

from sdk.contracts.forest import ForestRef
from sdk.contracts.models import ProjectOverlay
from sdk.features.items.models import (
    ItemCatalog,
    ItemCreationPlan,
    ItemEntry,
    ItemIcon,
    TextureRegion,
)


class ItemCatalogApi(Protocol):
    """Доменный контракт каталога предметов (capability ``items.catalog``)."""

    def item_catalog_root(self, forest: ForestRef) -> Optional[Path]:
        """Корень каталога предметов: ``system.ltx`` или fallback-файл."""

    def build_item_catalog(
        self,
        forest: ForestRef,
        log: Optional[Callable[[str], None]] = None,
        resolved_text: Optional[str] = None,
        reuse_snapshot_cache: bool = False,
        overlay: Optional[ProjectOverlay] = None,
    ) -> ItemCatalog:
        """Собрать каталог предметов из резолва ``system.ltx``.

        Основной корень — ``system.ltx`` (учитывает ``mod_system_*.ltx``);
        для урезанных VFS возможен fallback на ``items.ltx``/
        ``item_base.ltx``. Результат отсортирован по имени секции.

        ``resolved_text`` — готовый текст (черновик проекта); тогда резолв
        не выполняется, а кэш ключуется хэшем текста.

        ``overlay`` — правки проекта: названия и описания читаются из
        XML-черновиков, поэтому карточка показывает те же данные, которые
        попадут в патч.
        """

    def build_item_entry(
        self,
        forest: ForestRef,
        resolved_text: str,
        section: str,
        log: Optional[Callable[[str], None]] = None,
        overlay: Optional[ProjectOverlay] = None,
    ) -> Optional[ItemEntry]:
        """Собрать один предмет из текущего resolved-текста.

        Дешёвый путь для карточки: разбирается только блок секции.
        Возвращает ``None``, если секция не найдена или не является
        предметом.
        """

    def build_item_entry_from_block(
        self,
        forest: ForestRef,
        block_text: str,
        section: str,
        offset: int = 0,
        log: Optional[Callable[[str], None]] = None,
        overlay: Optional[ProjectOverlay] = None,
    ) -> Optional[ItemEntry]:
        """Собрать предмет из готового блока секции и смещения строк."""

    def edit_localization_text(self, text: str, key: str, value: str):
        """Изменить ``<text>`` строки ``<string id="key">`` в XML.

        Возвращает новый текст XML или ``None``, если документ не похож
        на строковую таблицу. Остальные строки и форматирование не
        меняются; результат затем записывается в черновик проекта.
        """

    def read_texture_region(
        self,
        path: Path,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> TextureRegion:
        """Декодировать прямоугольник DDS-текстуры в RGBA.

        Используется для вырезания иконки из атласа по ``inv_grid_*``.
        Поддерживаются несжатые DDS и DXT1/DXT3/DXT5.
        """

    def load_item_icons(
        self,
        catalog: ItemCatalog,
        log: Optional[Callable[[str], None]] = None,
        progress: Optional[Callable] = None,
    ) -> tuple[ItemIcon, ...]:
        """Пакетно декодировать иконки каталога.

        Ядро использует кэш регионов и пул процессов; модуль получает
        готовые RGBA-иконки и не занимает UI-поток декодированием DDS.
        ``progress(done, total, message)`` — необязательный структурированный
        прогресс; ``log`` остаётся для текстовых сообщений.
        """

    def item_entry(self, tab_id: str, section: str):
        """Текущая проекция предмета из документа или ``None``."""

    def plan_item_creation(
        self,
        item_text: str,
        xml_text: str,
        known_sections,
        section: str,
        parent,
        parent_fields,
        project_name,
    ) -> ItemCreationPlan:
        """Спланировать новый предмет без записи файлов.

        Возвращает готовые тексты секции и XML-локализации; запись в
        проект выполняет вызывающая сторона.
        """

    def creation_paths(self, scan_dir, project_name):
        """Пути файлов проекта для нового предмета."""

    def parent_fields(self, catalog, parent):
        """Поля родительской секции/предмета для новой секции."""
