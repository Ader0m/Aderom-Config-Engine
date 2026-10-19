"""reference_graph — сервис ядра Aderom Config Engine.

Публичный контракт (только чтение, через SDK):
- ReferenceGraphBuilder.get_section_graph(section) -> GraphSnapshot | GraphDiagnostic;
- события graph_updated / graph_index_rebuilt через шину (10.8);
- все возвращаемые структуры неизменяемы (frozen dataclass).
"""
from .adapters import (
    ForestStructureAdapter,
    ParsedRawAdapter,
    ResolverTreeAdapter,
    create_graph_input,
)
from .builder import ReferenceGraphBuilder, assemble_graph, RULES_VERSION
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
from .models import (
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
from .resolved_index import ResolvedMentionIndex, build_index
from .tokenizer import MatchKind, match_token, normalize_name, tokenize

__all__ = [
    # Builder
    "ReferenceGraphBuilder",
    "assemble_graph",
    "RULES_VERSION",
    # Index
    "ResolvedMentionIndex",
    "build_index",
    # Tokenizer
    "MatchKind",
    "match_token",
    "normalize_name",
    "tokenize",
    # Models
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
    # GraphInput
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
    # Adapters
    "ForestStructureAdapter",
    "ParsedRawAdapter",
    "ResolverTreeAdapter",
    "create_graph_input",
]