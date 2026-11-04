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

from .models import LtxForest, LineSource

from .fs import (
    RESOLVER_CANDIDATES,
    TEXT_FILE_SUFFIXES,
    normalize_path,
    canonical_config_dir,
    rel_path,
    pattern_match,
    read_text_safe,
    read_xml_text,
    strip_comments,
    extract_includes,
    resolve_include_targets,
    scan_files,
    scan_ltx_files,
    scan_xml_files,
    find_system_ltx,
    build_search_bases,
    find_resolver_script,
    load_resolver_module,
    get_dltx_mods_for_root,
    build_source_map,
    pretty_source,
)

from .external import open_in_notepad_pp, open_with_shell
from .vfs_paths import resolve_physical_path

from .file_cache import FileCacheSystem
from .resolved_cache import ResolvedCache, invalidate_forest_signatures

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

from .documents.document_model import (
    LtxDocument,
    LtxEntry,
    LtxSectionModel,
    SectionView,
    parse_ltx_document,
    parse_ltx_file,
)
from .documents.document_diff import (
    DocumentChange,
    DocumentDiff,
    DiffIssue,
    SectionSnapshot,
    diff_documents,
    diff_texts,
)
from .documents.document_session import DocumentSession, FileDocumentTracker
from .documents.resolved_edit import (
    ResolvedEditAnalysis,
    ResolvedLineChange,
    analyze_resolved_changes,
)
from .documents.single_line_edit import (
    SingleLineEditAnalysis,
    analyze_single_line_edit,
)
from .documents.multiline_edit import (
    EditConflict,
    MultiLineEditAnalysis,
    analyze_multiline_edit,
)
from .patching.patch_models import (
    GeneratedPatch,
    PatchFile,
    PatchIssue,
    PatchOptions,
    PatchPlan,
    PatchTarget,
    VerificationCheck,
    VerificationResult,
)
from .patching.patch_targets import (
    choose_load_last_suffix,
    patch_file_name,
    patch_file_rel,
    patch_mod_file_rel,
    resolve_patch_target,
    root_candidates,
    suggest_patch_mod_name,
    unique_suffix,
    validate_patch_mod_root,
)
from .patching.patch_merge import merge_patch_plans
from .patching.patch_planner import PatchPlanner, PatchPlanningError
from .patching.dltx_generator import (
    DltxGenerator,
    PatchGenerationError,
    render_dltx,
)
from .patching.dxml_generator import DxmlGenerator
from .patching.patch_verifier import (
    ModelPatchVerifier,
    ResolverPatchVerifier,
    apply_patch_document,
)
from .patching.patch_writer import PatchWriteError, PatchWriter
from .reference_graph import ReferenceGraphBuilder
from .xml_slaxml import (
    SlaxmlParser,
    SlaxmlError,
    SourceRef,
    assign_origins,
    pretty_dom,
    pretty_xml,
)
from .xml_dxml import DxmlProcessor, DxmlError, XmlObject
from .xml_resolver import XmlResolver, XmlResolveOutput, XmlResolveError
from .xml_resolve_service import XmlResolveService


__all__ = [
    # Модели
    "LtxForest",
    "LineSource",

    # fs
    "RESOLVER_CANDIDATES",
    "TEXT_FILE_SUFFIXES",
    "normalize_path",
    "canonical_config_dir",
    "rel_path",
    "pattern_match",
    "read_text_safe",
    "read_xml_text",
    "strip_comments",
    "extract_includes",
    "resolve_include_targets",
    "scan_files",
    "scan_ltx_files",
    "scan_xml_files",
    "find_system_ltx",
    "build_search_bases",
    "find_resolver_script",
    "load_resolver_module",
    "get_dltx_mods_for_root",
    "build_source_map",
    "pretty_source",

    # external
    "open_in_notepad_pp",
    "open_with_shell",
    "resolve_physical_path",

    # cache
    "FileCacheSystem",
    "ResolvedCache",
    "invalidate_forest_signatures",

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

    # documents and diff
    "LtxDocument",
    "LtxEntry",
    "LtxSectionModel",
    "SectionView",
    "parse_ltx_document",
    "parse_ltx_file",
    "DocumentChange",
    "DocumentDiff",
    "DiffIssue",
    "SectionSnapshot",
    "diff_documents",
    "diff_texts",
    "DocumentSession",
    "FileDocumentTracker",
    "ResolvedEditAnalysis",
    "ResolvedLineChange",
    "analyze_resolved_changes",
    "SingleLineEditAnalysis",
    "analyze_single_line_edit",
    "EditConflict",
    "MultiLineEditAnalysis",
    "analyze_multiline_edit",

    # patching
    "PatchPlanner",
    "PatchPlanningError",
    "merge_patch_plans",
    "DltxGenerator",
    "PatchGenerationError",
    "render_dltx",
    "DxmlGenerator",
    "PatchTarget",
    "PatchOptions",
    "PatchPlan",
    "PatchIssue",
    "PatchFile",
    "GeneratedPatch",
    "VerificationCheck",
    "VerificationResult",
    "ModelPatchVerifier",
    "ResolverPatchVerifier",
    "apply_patch_document",
    "PatchWriter",
    "PatchWriteError",
    "resolve_patch_target",
    "choose_load_last_suffix",
    "root_candidates",
    "patch_file_name",
    "patch_file_rel",
    "patch_mod_file_rel",
    "suggest_patch_mod_name",
    "unique_suffix",
    "validate_patch_mod_root",

    # graph
    "ReferenceGraphBuilder",

    # xml / dxml
    "SlaxmlParser",
    "SlaxmlError",
    "SourceRef",
    "assign_origins",
    "pretty_dom",
    "pretty_xml",
    "DxmlProcessor",
    "DxmlError",
    "XmlObject",
    "XmlResolver",
    "XmlResolveOutput",
    "XmlResolveError",
    "XmlResolveService",
]