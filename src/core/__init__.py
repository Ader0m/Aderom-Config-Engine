# -*- coding: utf-8 -*-
"""
core — ядро приложения.

Этот пакет отвечает за:
- дерево зависимостей файлов;
- запросы к дереву;
- подключение внешнего резолвера;
- кэш;
- каркас планирования патчей;
- каркас генерации DLTX/DXML;
- каркас графа связей.
"""

from .models import LtxForest

from .fs import (
    RESOLVER_CANDIDATES,
    normalize_path,
    rel_path,
    pattern_match,
    read_text_safe,
    strip_comments,
    extract_includes,
    resolve_include_targets,
    scan_ltx_files,
    find_system_ltx,
    build_search_bases,
    find_resolver_script,
    load_resolver_module,
    get_dltx_mods_for_root,
    build_source_map,
    pretty_source,
)

from .external import open_in_notepad_pp

from .file_cache import FileCacheSystem

from .tree_builder import (
    TreeBuilder,
    build_forest,
)

from .tree_query_service import (
    TreeQueryService,
    node_sort_key,
    sort_nodes,
    search_nodes,
    generate_report,
)

from .resolver_adapter import (
    LtxResolverAdapter,
    Assembler,
)

from .patch_planner import PatchPlanner
from .dltx_generator import DltxGenerator
from .dxml_generator import DxmlGenerator
from .reference_graph import ReferenceGraphBuilder


__all__ = [
    # Модели
    "LtxForest",

    # fs
    "RESOLVER_CANDIDATES",
    "normalize_path",
    "rel_path",
    "pattern_match",
    "read_text_safe",
    "strip_comments",
    "extract_includes",
    "resolve_include_targets",
    "scan_ltx_files",
    "find_system_ltx",
    "build_search_bases",
    "find_resolver_script",
    "load_resolver_module",
    "get_dltx_mods_for_root",
    "build_source_map",
    "pretty_source",

    # external
    "open_in_notepad_pp",

    # cache
    "FileCacheSystem",

    # tree
    "TreeBuilder",
    "build_forest",

    # queries
    "TreeQueryService",
    "node_sort_key",
    "sort_nodes",
    "search_nodes",
    "generate_report",

    # resolver
    "LtxResolverAdapter",
    "Assembler",

    # patching
    "PatchPlanner",
    "DltxGenerator",
    "DxmlGenerator",

    # graph
    "ReferenceGraphBuilder",
]