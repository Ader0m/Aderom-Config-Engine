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
from .vfs_paths import resolve_asset_path, resolve_physical_path
from .dds import DdsError, DdsInfo, TextureRegion, read_dds_info, read_dds_region
from .item_catalog import (
    DEFAULT_ICON_TEXTURE,
    ITEM_CATALOG_VERSION,
    GRID_SIZE,
    ItemCatalog,
    ItemCatalogService,
    ItemField,
    ItemRecord,
)
from .item_icons import ICON_CACHE_VERSION, ItemIconService
from .localization import (
    LOCALIZATION_VERSION,
    LocalizationService,
    LocalizationTable,
)
from .object_cache import ObjectCache

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
from .documents.xml_model import (
    XmlDocument,
    XmlModelError,
    XmlNode,
    parse_xml_document,
    parse_xml_file,
    serialize_xml_document,
)
from .documents.xml_diff import (
    XmlChange,
    XmlDiffIssue,
    XmlDocumentDiff,
    apply_xml_changes,
    diff_xml_documents,
    diff_xml_texts,
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
from .patching.xml_patch_verifier import (
    XmlModelPatchVerifier,
    XmlResolverPatchVerifier,
    apply_changes_to_xml_obj,
)
from .patching.xml_fork import XmlForkError, XmlForkService, XmlForkResult
from .patching.ltx_override import LtxOverrideError, LtxOverrideResult, LtxOverrideService
from .patching.ltx_in_place import (
    LtxInPlaceError,
    LtxInPlaceResult,
    LtxInPlaceService,
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
    "resolve_asset_path",
    "resolve_physical_path",

    # items / dds
    "DEFAULT_ICON_TEXTURE",
    "ITEM_CATALOG_VERSION",
    "GRID_SIZE",
    "ItemCatalog",
    "ItemCatalogService",
    "ItemField",
    "ItemRecord",
    "ICON_CACHE_VERSION",
    "ItemIconService",
    "LOCALIZATION_VERSION",
    "LocalizationService",
    "LocalizationTable",
    "ObjectCache",
    "DdsError",
    "DdsInfo",
    "TextureRegion",
    "read_dds_info",
    "read_dds_region",

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
    "XmlNode",
    "XmlDocument",
    "XmlModelError",
    "parse_xml_document",
    "parse_xml_file",
    "serialize_xml_document",
    "XmlChange",
    "XmlDiffIssue",
    "XmlDocumentDiff",
    "diff_xml_documents",
    "diff_xml_texts",
    "apply_xml_changes",

    # patching
    "PatchPlanner",
    "PatchPlanningError",
    "merge_patch_plans",
    "DltxGenerator",
    "PatchGenerationError",
    "render_dltx",
    "DxmlGenerator",
    "XmlModelPatchVerifier",
    "XmlResolverPatchVerifier",
    "apply_changes_to_xml_obj",
    "XmlForkService",
    "XmlForkResult",
    "XmlForkError",
    "LtxOverrideService",
    "LtxOverrideResult",
    "LtxOverrideError",
    "LtxInPlaceService",
    "LtxInPlaceResult",
    "LtxInPlaceError",
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