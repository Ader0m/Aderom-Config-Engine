# -*- coding: utf-8 -*-
"""Модели данных ядра."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Set


@dataclass(frozen=True)
class LineSource:
    """Владелец строки итогового текста.

    Это доменная модель, а не формат панели UI:
    - ``kind``: ``original``, ``file``, ``patch``;
    - ``display``: человекочитаемая подпись (без UI-маркеров);
    - ``path``: файл для открытия, если есть;
    - ``line``: строка в исходнике, если известна;
    - ``open_raw``: открывать исходник без резолва.
    """

    kind: str = "none"
    display: str = ""
    path: Optional[Path] = None
    line: int = 0
    open_raw: bool = False


@dataclass
class LtxForest:
    """
    Объективное дерево зависимостей LTX-файлов.

    Это базовая структура, которую строит ядро.
    Она не обязана совпадать с видимым деревом в UI:
    интерфейс может применять к ней проекции, фильтры и сортировки.
    """

    scan_dir: str
    system_root: str
    total_files: int
    files: list
    rel_paths: dict
    roots: list
    roots_set: set
    display_children: dict
    node_parent: dict
    subtree_counts: dict
    system_tree_files: int

    # Количество секций, полученное через внешний резолвер.
    # Поле оставлено для совместимости со старым кодом.
    resolver_sections: object = None

    # Дополнительные зависимости для будущего графа связей.
    # Это не новая бизнес-логика, а только сохранение тех связей,
    # которые уже вычислялись в старом build_forest.
    include_children: Dict[object, Set[object]] = field(default_factory=dict)
    include_parents: Dict[object, Set[object]] = field(default_factory=dict)
    dltx_attachments: Dict[object, Set[object]] = field(default_factory=dict)
    real_paths: Dict[object, str] = field(default_factory=dict)

    # XML-файлы VFS. Они есть в дереве для просмотра, но не участвуют
    # в include-связях, DLTX-патчах и проходе резолвера.
    xml_files: list = field(default_factory=list)
    total_xml_files: int = 0

    @property
    def all_files(self) -> list:
        """Все файлы дерева: LTX + XML (для поиска и обхода UI)."""
        return list(self.files) + list(self.xml_files)
