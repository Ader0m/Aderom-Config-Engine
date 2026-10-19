"""Фаза B reference_graph: сборка звезды вокруг центральной секции + сервис ядра.

Правила сборки (спецификация v3):
- центр есть в итоговом слепке -> GraphSnapshot; нет -> GraphDiagnostic
  («секция удалена или не существовала», фантомный центр запрещён, 8.6);
- файловый узел = файл итогового дерева, в котором упоминание найдено (без метаданных);
- одно ребро на пару (центр, узел): сила = max, метка = метка сильнейшего упоминания;
- PARENT/CHILD -> из финальной цепочки наследования (после :!, !, !!), сила HIGH;
- IMAGE -> из ключей центра со значениями-путями (icon/visual/texture), сила MEDIUM;
- SourceMeta (владелец, диск-путь, цепочка) пробрасывается в упоминаниях непрозрачно;
- циклы и висячие родители -> warnings: логируем мягко, не падаем (поведение движка);
- доказательства v1 — уровень файла; сокращение до строк — не ответственность билдера (8.2).
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Tuple

from .models import (DiagnosticKind, EdgeLabel, GraphDiagnostic, GraphEdge,
                     GraphNode, GraphResult, GraphSnapshot, Mention,
                     MentionOrigin, NodeType, RULES_VERSION, Strength,
                     Versions, node_id)

from .resolved_index import ResolvedMentionIndex, GraphInput, build_index
from .tokenizer import looks_like_resource_path, normalize_name

_ORIGIN_NODE_TYPE = {
    MentionOrigin.XML: NodeType.XML_FILE,
    MentionOrigin.SCRIPT: NodeType.SCRIPT,
}

class CachePort(Protocol):
    """Порт FileCacheSystem: граф не знает конкретную реализацию кэша (6.6).
    """
    def get(self, key: str, default: Any = None) -> Any: ...
    def set(self, key: str, value: Any) -> None: ...


class EventPort(Protocol):
    """Порт шины событий: уведомления — событиями, не прямыми вызовами (10.8)."""
    def emit(self, event: str, payload: Mapping[str, Any]) -> None: ...


# --------------------------------------------------------------------------- #
# Чистая сборка
# --------------------------------------------------------------------------- #

def _section_node(name: str, keys: Mapping[str, str]) -> GraphNode:
    return GraphNode(NodeType.SECTION, f"[{name}]", name, resolved_keys=keys)


def _inheritance_cycle(center: str, index: ResolvedMentionIndex) -> Optional[Tuple[str, str]]:
    """Цикл через центр: подъём по родителям до повторного входа в центр."""
    visited = set()
    stack = list(index.parents_of(center))
    while stack:
        current = stack.pop()
        if current == center:
            return center, current
        if current in visited:
            continue
        visited.add(current)
        for parent in index.parents_of(current):
            if parent == center:
                return center, current
            stack.append(parent)
    return None


def assemble_graph(section: str, index: ResolvedMentionIndex) -> GraphResult:
    """Фаза B: дешёвая сборка снимка звезды из индекса фазы A."""
    name = normalize_name(section)

    if not index.contains(name):
        detail = (DiagnosticKind.SECTION_REMOVED if index.was_raw_defined(name)
                  else DiagnosticKind.SECTION_NEVER_EXISTED)
        return GraphDiagnostic(section=name, detail=detail)

    nodes: Dict[str, GraphNode] = {}
    edges: List[GraphEdge] = []
    warnings: List[str] = []
    files: List[str] = []

    center = _section_node(name, index.keys_of(name))
    nodes[center.id] = center

    # Наследование: только финальная цепочка.
    for parent in index.parents_of(name):
        if index.contains(parent):
            node = _section_node(parent, index.keys_of(parent))
            nodes.setdefault(node.id, node)
            edges.append(GraphEdge(center.id, node.id, EdgeLabel.PARENT, Strength.HIGH))
        else:
            warnings.append(f"Parent '{parent}' of section '{name}' not found in snapshot.")
    for child in index.children_of(name):
        if index.contains(child):
            node = _section_node(child, index.keys_of(child))
            nodes.setdefault(node.id, node)
            edges.append(GraphEdge(center.id, node.id, EdgeLabel.CHILD, Strength.HIGH))

    cycle = _inheritance_cycle(name, index)
    if cycle is not None:
        warnings.append(f"Circular reference detected between {cycle[0]} and {cycle[1]}.")

    # Упоминания: группировка по (тип узла, файл дерева) -> одно ребро на группу.
    groups: Dict[Tuple[NodeType, str], List[Mention]] = {}
    for mention in index.mentions_for(name):
        node_type = _ORIGIN_NODE_TYPE.get(mention.origin, NodeType.LTX_FILE)
        groups.setdefault((node_type, mention.tree_file), []).append(mention)

    for (node_type, tree_file), mentions in groups.items():
        node = GraphNode(node_type, tree_file, tree_file)
        nodes.setdefault(node.id, node)
        best = mentions[0]
        for mention in mentions[1:]:
            if mention.strength > best.strength:
                best = mention
        edges.append(GraphEdge(node.id, center.id, best.label, best.strength,
                               tuple(mentions)))
        files.append(tree_file)

    # Image-рёбра: ключи центра со значениями-путями ресурсов.
    for key, value in center.resolved_keys.items():
        if looks_like_resource_path(key, value):
            path = value.strip()
            node = GraphNode(NodeType.RESOURCE, path, path)
            if node.id not in nodes:
                nodes[node.id] = node
                edges.append(GraphEdge(center.id, node.id, EdgeLabel.IMAGE, Strength.MEDIUM))

    return GraphSnapshot(
        center_id=center.id,
        nodes=nodes,
        edges=tuple(edges),
        files=tuple(sorted(files)),
        warnings=tuple(warnings),
        versions=index.versions,
    )


# --------------------------------------------------------------------------- #
# Сервис ядра
# --------------------------------------------------------------------------- #

class ReferenceGraphBuilder:
    """Сервис ядра: оркестрация фазы A (индекс, кэш) и фазы B (сборка).

    Нарушение кэша/инвалидация: ключ содержит версии снимка VFS, парсера,
    резолвера и правил — смена любой версии даёт новый ключ, устаревший
    индекс не используется (аналог 6.6/9.11 для графа).
    Наружу отдаются только неизменяемые снимки: read-only контракт SDK (10.9).
    """

    def __init__(self, input_factory: Callable[[], GraphInput],
                 cache: Optional[CachePort] = None,
                 events: Optional[EventPort] = None) -> None:
        self._input_factory = input_factory
        self._cache = cache
        self._events = events
        self._index: Optional[ResolvedMentionIndex] = None
        self._index_key: Optional[str] = None

    @staticmethod
    def _cache_key(versions: Versions) -> str:
        return (f"reference_graph:index:{versions.vfs_snapshot}:{versions.parser}:"
                f"{versions.resolver}:{RULES_VERSION}")

    def _emit(self, event: str, payload: Mapping[str, Any]) -> None:
        if self._events is not None:
            self._events.emit(event, payload)

    def invalidate(self) -> None:
        """Явный сброс (смена снимка VFS снаружи)."""
        self._index = None
        self._index_key = None

    def current_index(self) -> ResolvedMentionIndex:
        """Индекс фазы A: из памяти, из кэша, либо построение + публикация события."""
        source = self._input_factory()
        versions = source.versions()
        key = self._cache_key(versions)
        if self._index is not None and self._index_key == key:
            return self._index
        if self._cache is not None:
            cached = self._cache.get(key)
            if cached is not None:
                self._index, self._index_key = cached, key
                return cached
        index = build_index(source)
        if self._cache is not None:
            self._cache.set(key, index)
        self._index, self._index_key = index, key
        self._emit("graph_index_rebuilt", {"mention_count": index.mention_count})
        return index

    def get_section_graph(self, section: str) -> GraphResult:
        """Основной запрос (сценарий 8.3, шаги 4-6): звезда или диагностика."""
        index = self.current_index()
        result = assemble_graph(section, index)
        if isinstance(result, GraphSnapshot):
            self._emit("graph_updated", {
                "section": normalize_name(section),
                "nodes": result.node_count,
                "edges": result.edge_count,
                "files": result.files,
                "warnings": result.warnings,
            })
        return result