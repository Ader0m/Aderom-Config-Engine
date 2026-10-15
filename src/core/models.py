# -*- coding: utf-8 -*-
"""Модели данных ядра."""

from dataclasses import dataclass, field
from typing import Dict, Set


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