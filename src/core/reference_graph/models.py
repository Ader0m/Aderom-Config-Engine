"""Модели данных сервиса reference_graph (ядро Aderom Config Engine).

Граф — «звезда» вокруг центральной секции. Зафиксированные решения v3:
- центр = резолв-снимок секции из итогового дерева Resolver4;
- файловые узлы = файлы итогового дерева, в которых при обходе найдены упоминания;
- владелец строки и путь до реального файла на диске НЕ влияют на топологию,
  имена узлов и метки — они проходят непрозрачным блоком SourceMeta («не используем, но не теряем»);
- доказательства v1 — уровень файла (строки — ответственность последующей постобработки, не билдера);
- если секции нет в итоговом слепке — возвращается Diagnostic, граф не строится.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from types import MappingProxyType
from typing import Mapping, Optional, Tuple, Union

RULES_VERSION = "3.0"  # версия правил классификации; менять при изменении таблиц меток

class NodeType(Enum):
    """Тип узла графа."""
    SECTION = "section"        # секция (центр или сосед по наследованию)
    LTX_FILE = "ltx_file"      # файл итогового дерева, где найдено упоминание
    XML_FILE = "xml_file"      # xml-файл (отдельный узел, решение 3)
    SCRIPT = "script"          # скрипт (каждый скрипт — отдельный узел, решение 4)
    RESOURCE = "resource"      # ресурс изображения (icon/visual/texture)


class EdgeLabel(Enum):
    """Плоский перечень меток ребра (решение 2): только метка по типу связи."""
    PARENT = "parent"
    CHILD = "child"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    XML = "xml"
    IMAGE = "image"
    SCRIPT = "script"


class Strength(IntEnum):
    """Сила связи — определяет цвет ребра (решение 1). Порядок = возрастание силы."""
    LOW = 1
    MEDIUM = 2
    HIGH = 3


class MentionOrigin(Enum):
    """Где именно найдено упоминание (влияет на метку через классификатор)."""
    KEY = "key"            # имя ключа в итоговом дереве
    DEFINITION = "definition"  # файл владеет итоговыми строками секции (объявление/переопределение)
    VALUE = "value"        # значение/список в итоговом дереве
    COMMENT = "comment"    # комментарий '; текст' (сырые файлы) -> low
    FILENAME = "filename"  # имя файла (сырые данные) -> low
    XML = "xml"            # xml-файл (сырой скан)
    SCRIPT = "script"      # скрипт (сырой скан)


@dataclass(frozen=True)
class ChainLink:
    """Одна попытка записать ключ (элемент цепочки переопределений)."""
    file: str
    depth: int
    value: str
    operator: str = ""
    insert_index: int = 0


@dataclass(frozen=True)
class SourceMeta:
    """Непрозрачный блок сопровождения строки.

    Билдер ЗАПИСЫВАЕТ его и НЕ ЧИТАЕТ в v1. Потребители — последующие этапы
    обработки построенного графа (сокращение файлов до целевых строк,
    карточка предмета, планирование патчей, режим «источники переопределений»).
    """
    owner: Optional[str] = None                 # итоговый владелец строки (имя файла)
    disk_path: Optional[str] = None             # путь до реального файла на диске
    chain: Tuple[ChainLink, ...] = ()           # цепочка переопределений целиком


@dataclass(frozen=True)
class Mention:
    """Упоминание искомой секции. Доказательство v1 — уровень файла."""
    target_section: str
    tree_file: str                        # файл итогового дерева -> имя файлового узла
    user_section: Optional[str]           # секция внутри файла, где лежит строка
    key: Optional[str]                    # ключ строки (None для комментариев/имён)
    origin: MentionOrigin
    label: EdgeLabel
    strength: Strength
    meta: Optional[SourceMeta] = None     # пишется, не читается


def node_id(node_type: NodeType, path: str) -> str:
    """Стабильный идентификатор узла. Детерминирован (воспроизводимость снимка)."""
    return f"{node_type.value}:{path}"


@dataclass(frozen=True)
class GraphNode:
    type: NodeType
    name: str                              # отображение: '[raid_material_1]' или 'craft.ltx'
    path: str                              # идентификатор в дереве / имя секции / путь ресурса
    resolved_keys: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )                                    # только для центра: финальные ключ-значения

    @property
    def id(self) -> str:
        return node_id(self.type, self.path)


@dataclass(frozen=True)
class GraphEdge:
    """Одно ребро на пару (центр, узел). Сила = max по упоминаниям, все упоминания сохранены."""
    source: str                            # node_id
    target: str                            # node_id
    label: EdgeLabel
    strength: Strength
    mentions: Tuple[Mention, ...] = ()


@dataclass(frozen=True)
class Versions:
    """Ключ кэша снимка графа: всё, что меняет результат."""
    vfs_snapshot: str
    parser: str
    resolver: str
    rules: str


class DiagnosticKind(Enum):
    SECTION_REMOVED = "removed"              # определение в сырых файлах есть, в слепке нет
    SECTION_NEVER_EXISTED = "never_existed"  # определения нет нигде


@dataclass(frozen=True)
class GraphDiagnostic:
    """Результат «графа нет». Фантомный центр запрещён (решение 8.6)."""
    section: str
    message: str = "секция удалена или не существовала"
    detail: Optional[DiagnosticKind] = None


@dataclass(frozen=True)
class GraphSnapshot:
    """Итог фазы B: звезда вокруг центра + зафиксированные файлы-участники."""
    center_id: str
    nodes: Mapping[str, GraphNode]
    edges: Tuple[GraphEdge, ...]
    files: Tuple[str, ...]                   # «сохранить файлы, вошедшие в граф» (8.2)
    warnings: Tuple[str, ...]                # циклы, висячие родители — мягко, без падений
    versions: Versions

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return len(self.edges)


GraphResult = Union[GraphSnapshot, GraphDiagnostic]