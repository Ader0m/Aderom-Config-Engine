# -*- coding: utf-8 -*-
"""reference_graph — сервис ядра Aderom Config Engine.

Публичный контракт (только чтение, через SDK):
- ReferenceGraphBuilder.get_section_graph(section) -> GraphSnapshot | GraphDiagnostic;
- события graph_updated / graph_index_rebuilt через шину (10.8);
- все возвращаемые структуры неизменяемы (frozen dataclass).
"""
from .models import (
    RULES_VERSION,
    ChainLink,
    DiagnosticKind,
    EdgeLabel,
    GraphDiagnostic,
    GraphEdge,
    GraphNode,
    GraphResult,
    GraphSnapshot,
    Mention,
    MentionOrigin,
    NodeType,
    SourceMeta,
    Strength,
    Versions,
)
from .tokenizer import (
    MatchKind,
    looks_like_resource_path,
    match_token,
    normalize_name,
    split_comment,
    tokenize,
)
from .classifier import aggregate_edge, classify
from .resolved_index import ResolvedMentionIndex, build_index, resolve_target
from .graph_input import (
    GraphInputSnapshot,
    Line,
    RawComment,
    ResolvedSection,
    ScriptFile,
    TreeFile,
    TreeSection,
    VersionSkewError,
    XmlFile,
    assemble_graph_input,
)
from .builder import ReferenceGraphBuilder, assemble_graph
from .adapters import (
    ForestStructureAdapter,
    ParsedRawAdapter,
    ResolverTreeAdapter,
    create_graph_input,
)

__all__ = [
    "RULES_VERSION",
    "ChainLink",
    "DiagnosticKind",
    "EdgeLabel",
    "GraphDiagnostic",
    "GraphEdge",
    "GraphNode",
    "GraphResult",
    "GraphSnapshot",
    "Mention",
    "MentionOrigin",
    "NodeType",
    "SourceMeta",
    "Strength",
    "Versions",
    "MatchKind",
    "looks_like_resource_path",
    "match_token",
    "normalize_name",
    "split_comment",
    "tokenize",
    "aggregate_edge",
    "classify",
    "ResolvedMentionIndex",
    "build_index",
    "resolve_target",
    "GraphInputSnapshot",
    "Line",
    "RawComment",
    "ResolvedSection",
    "ScriptFile",
    "TreeFile",
    "TreeSection",
    "VersionSkewError",
    "XmlFile",
    "assemble_graph_input",
    "ReferenceGraphBuilder",
    "assemble_graph",
    "ForestStructureAdapter",
    "ParsedRawAdapter",
    "ResolverTreeAdapter",
    "create_graph_input",
]