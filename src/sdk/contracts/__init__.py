# -*- coding: utf-8 -*-
"""Общие контракты SDK: DTO, события, пути, read-only лес."""

from .constants import DEFAULT_SEARCH_DELAY_MS
from .events import Events, PLATFORM_OWNER, is_namespaced, namespace_of
from .forest import ForestRef, forest_ref, unwrap_forest
from .models import (
    FileOpenFailed,
    FileOpened,
    GraphEdgeStyle,
    GraphMatch,
    GraphNode,
    ProjectOverlay,
    SectionGraph,
    SectionGraphContribution,
    SectionGraphRequest,
    SnapshotChanged,
    TreeReady,
    TreeSummary,
)
from .paths import AppPaths

__all__ = [
    "DEFAULT_SEARCH_DELAY_MS",
    "Events",
    "PLATFORM_OWNER",
    "is_namespaced",
    "namespace_of",
    "ForestRef",
    "forest_ref",
    "unwrap_forest",
    "FileOpenFailed",
    "FileOpened",
    "GraphEdgeStyle",
    "GraphMatch",
    "GraphNode",
    "ProjectOverlay",
    "SectionGraph",
    "SectionGraphContribution",
    "SectionGraphRequest",
    "SnapshotChanged",
    "TreeReady",
    "TreeSummary",
    "AppPaths",
]
