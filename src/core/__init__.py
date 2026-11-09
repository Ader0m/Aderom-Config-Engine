# -*- coding: utf-8 -*-
"""
core — ядро приложения.

Публичная поверхность пакета: ``facade.py`` и ``models.py``. Остальное
сгруппировано по ответственности:

- ``tools`` — файлы, пути, кэш, процессы и текст;
- ``tree`` — дерево, запросы, граф, include/DLTX-модели;
- ``resolver`` — LTX-резолвер, сборка, предсборка, атрибуция;
- ``xml`` — SLAXML, DXML и XML-резолв;
- ``items`` — DDS, каталог предметов, иконки, локализация;
- ``patching/ltx`` и ``patching/xml`` — симметричные патч-конвейеры.
"""

from core.models import LtxForest, LineSource

from core.resolver.attribution import build_source_map, pretty_source
from core.tools.external import open_in_notepad_pp, open_with_shell
from core.tools.fs import (
    RESOLVER_CANDIDATES,
    TEXT_FILE_SUFFIXES,
    normalize_path,
    canonical_config_dir,
    rel_path,
    pattern_match,
    read_text_safe,
    read_xml_text,
    strip_comments,
    scan_files,
    scan_ltx_files,
    scan_xml_files,
    find_system_ltx,
    build_search_bases,
    find_resolver_script,
    load_resolver_module,
)
from core.tree.dltx_model import get_dltx_mods_for_root
from core.tree.include_model import extract_includes, resolve_include_targets

from core.tools.vfs import resolve_asset_path, resolve_physical_path
from core.items.dds import DdsError, DdsInfo, TextureRegion, read_dds_info, read_dds_region
from core.items.catalog import (
    DEFAULT_ICON_TEXTURE,
    ITEM_CATALOG_VERSION,
    GRID_SIZE,
    ItemCatalog,
    ItemCatalogService,
    ItemField,
    ItemRecord,
)
from core.items.icons import ICON_CACHE_VERSION, ItemIconService
from core.items.localization import (
    LOCALIZATION_VERSION,
    LocalizationService,
    LocalizationTable,
)
from core.tools.cache import (
    FileCacheSystem,
    ObjectCache,
    ResolvedCache,
    invalidate_forest_signatures,
)

from core.tree.builder import (
    TreeBuilder,
    build_forest,
)

from core.tree.query import (
    TreeQueryService,
    node_sort_key,
    sort_nodes,
    search_nodes,
    generate_report,
)

from core.resolver.ltx import (
    LtxResolverAdapter,
    Assembler,
)

from core.patching.ltx.document import (
    DocumentSession,
    FileDocumentTracker,
    LtxDocument,
    LtxEntry,
    LtxSectionModel,
    SectionView,
    parse_ltx_document,
    parse_ltx_file,
)
from core.patching.ltx.diff import (
    DocumentChange,
    DocumentDiff,
    DiffIssue,
    SectionSnapshot,
    diff_documents,
    diff_texts,
)
from core.patching.ltx.changes import (
    ResolvedEditAnalysis,
    ResolvedLineChange,
    SingleLineEditAnalysis,
    analyze_resolved_changes,
    analyze_single_line_edit,
)
from core.patching.ltx.multiline import (
    EditConflict,
    MultiLineEditAnalysis,
    analyze_multiline_edit,
)
from core.patching.xml.document import (
    XmlDocument,
    XmlModelError,
    XmlNode,
    parse_xml_document,
    parse_xml_file,
    serialize_xml_document,
)
from core.patching.xml.diff import (
    XmlChange,
    XmlDiffIssue,
    XmlDocumentDiff,
    apply_xml_changes,
    diff_xml_documents,
    diff_xml_texts,
)
from core.patching.models import (
    GeneratedPatch,
    PatchFile,
    PatchIssue,
    PatchOptions,
    PatchPlan,
    PatchResult,
    PatchTarget,
    VerificationCheck,
    VerificationResult,
)
from core.patching.ltx.service import LtxPatchService
from core.patching.xml.service import XmlPatchService
from core.patching.targets import (
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
from core.patching.ltx.merge import merge_patch_plans
from core.patching.planner import PatchPlanner, PatchPlanningError
from core.patching.ltx.generator import (
    DltxGenerator,
    PatchGenerationError,
    render_dltx,
)
from core.patching.xml.generator import DxmlGenerator
from core.patching.ltx.verifier import (
    ModelPatchVerifier,
    ResolverPatchVerifier,
    apply_patch_document,
)
from core.patching.xml.verifier import (
    XmlModelPatchVerifier,
    XmlResolverPatchVerifier,
)
from core.xml.changes import apply_changes_to_xml_obj
from core.patching.xml.fork import XmlForkError, XmlForkService, XmlForkResult
from core.patching.ltx.override import LtxOverrideError, LtxOverrideResult, LtxOverrideService
from core.patching.ltx.in_place import (
    LtxInPlaceError,
    LtxInPlaceResult,
    LtxInPlaceService,
)
from core.patching.writer import PatchWriteError, PatchWriter
from core.tree.graph import ReferenceGraphBuilder
from core.xml.slaxml import (
    SlaxmlParser,
    SlaxmlError,
    SourceRef,
    assign_origins,
    pretty_dom,
    pretty_xml,
)
from core.xml.dxml import DxmlProcessor, DxmlError, XmlObject
from core.xml.resolver import XmlResolver, XmlResolveOutput, XmlResolveError
from core.xml.service import XmlResolveService


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
    "LtxPatchService",
    "XmlPatchService",
    "PatchResult",
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