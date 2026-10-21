# -*- coding: utf-8 -*-
"""Фаза B reference_graph: сборка звезды вокруг центральной секции + сервис ядра."""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Tuple

from .classifier import aggregate_edge
from .models import (DiagnosticKind, EdgeLabel, GraphDiagnostic, GraphEdge,
                     GraphNode, GraphResult, GraphSnapshot, Mention,
                     MentionOrigin, NodeType, Strength)
from .resolved_index import GraphInput, ResolvedMentionIndex, build_index
from .tokenizer import looks_like_resource_path, normalize_name


class CachePort(Protocol):
    """Сигнатуры реального FileCacheSystem: get(key, default) / set(key, value)."""
    def get(self, key: str, default: Any = None) -> Any: ...
    def set(self, key: str, value: Any) -> None: ...


class EventPort(Protocol):
    def emit(self, event: str, payload: Mapping[str, Any]) -> None: ...


_ORIGIN_NODE_TYPE = {
    MentionOrigin.XML: NodeType.XML_FILE,
    MentionOrigin.SCRIPT: NodeType.SCRIPT,
}


def _section_node(name, keys):
    return GraphNode(NodeType.SECTION, "[%s]" % name, name, resolved_keys=keys)


def _inheritance_cycle(center, index):
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


def assemble_graph(section, index):
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

    for parent in index.parents_of(name):
        if index.contains(parent):
            node = _section_node(parent, index.keys_of(parent))
            nodes.setdefault(node.id, node)
            edges.append(GraphEdge(center.id, node.id, EdgeLabel.PARENT, Strength.HIGH))
        else:
            warnings.append(
                "Parent '%s' of section '%s' not found in snapshot." % (parent, name))
    for child in index.children_of(name):
        if index.contains(child):
            node = _section_node(child, index.keys_of(child))
            nodes.setdefault(node.id, node)
            edges.append(GraphEdge(center.id, node.id, EdgeLabel.CHILD, Strength.HIGH))

    cycle = _inheritance_cycle(name, index)
    if cycle is not None:
        warnings.append("Circular reference detected between %s and %s." % cycle)

    groups: Dict[Tuple[NodeType, str], List[Mention]] = {}
    for mention in index.mentions_for(name):
        node_type = _ORIGIN_NODE_TYPE.get(mention.origin, NodeType.LTX_FILE)
        groups.setdefault((node_type, mention.tree_file), []).append(mention)

    for (node_type, tree_file), mentions in groups.items():
        node = GraphNode(node_type, tree_file, tree_file)
        nodes.setdefault(node.id, node)
        files.append(tree_file)

        defs = tuple(m for m in mentions if m.origin is MentionOrigin.DEFINITION)
        refs = tuple(m for m in mentions if m.origin is not MentionOrigin.DEFINITION)

        if defs:
            label, strength = aggregate_edge(defs)
            edges.append(GraphEdge(center.id, node.id, label, strength, defs))
        if refs:
            label, strength = aggregate_edge(refs)
            edges.append(GraphEdge(node.id, center.id, label, strength, refs))

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


class ReferenceGraphBuilder:
    def __init__(self, input_factory: Callable[[], GraphInput],
                 cache: Optional[CachePort] = None,
                 events: Optional[EventPort] = None) -> None:
        self._input_factory = input_factory
        self._cache = cache
        self._events = events
        self._index: Optional[ResolvedMentionIndex] = None
        self._index_key: Optional[str] = None

    @staticmethod
    def _cache_key(versions) -> str:
        return ("reference_graph:index:%s:%s:%s:%s"
                % (versions.vfs_snapshot, versions.parser,
                   versions.resolver, versions.rules))

    def _emit(self, event: str, payload: Mapping[str, Any]) -> None:
        if self._events is not None:
            self._events.emit(event, payload)

    def invalidate(self) -> None:
        self._index = None
        self._index_key = None

    def current_index(self) -> ResolvedMentionIndex:
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