# -*- coding: utf-8 -*-
"""Общие преобразования bridge-слоя SDK."""

from __future__ import annotations

import logging
from pathlib import Path

from sdk.contracts.models import (
    GraphMatch,
    GraphNode,
    SectionGraph,
    SectionGraphContribution,
)
from sdk.features.items.models import (
    ItemCatalog,
    ItemEntry,
    ItemField,
    ItemIcon,
    ItemSection,
    LocalizationString,
    TextureRegion,
)
from sdk.features.patches.models import PatchTargetInfo

log = logging.getLogger(__name__)

_SDK_API_MARKER = "_sdk_api_kind"


def _to_patch_target_info(value) -> PatchTargetInfo:
    if value is None:
        return PatchTargetInfo(root_path=None)
    if isinstance(value, PatchTargetInfo):
        return value
    root = getattr(value, "root_path", None)
    if root is not None:
        root = Path(root)
    return PatchTargetInfo(
        root_path=root,
        candidates=tuple(
            Path(path)
            for path in (getattr(value, "candidates", ()) or ())
        ),
        reason=str(getattr(value, "reason", "") or ""),
        ambiguous=bool(getattr(value, "ambiguous", False)),
    )


def _to_localization_string(value):
    if value is None:
        return None

    path = getattr(value, "path", None)
    if path is not None:
        path = Path(path)

    return LocalizationString(
        key=str(getattr(value, "key", "") or ""),
        text=str(getattr(value, "text", "") or ""),
        path=path,
        line=int(getattr(value, "line", 0) or 0),
        raw_text=str(getattr(value, "raw_text", "") or ""),
    )


def _to_item_entry(item) -> ItemEntry:
    """Единая конверсия core/SDK-записи предмета в SDK-модель.

    Используется и CoreApiAdapter, и UiApiAdapter: модуль не должен
    получать core-модель через один API и SDK-модель через другой.
    """
    icon_path = getattr(item, "icon_path", None)
    if icon_path is not None:
        icon_path = Path(icon_path)

    return ItemEntry(
        section=str(getattr(item, "section", "")),
        fields=tuple(
            ItemField(
                key=str(getattr(field, "key", "")),
                value=str(getattr(field, "value", "")),
                line_number=int(
                    getattr(field, "line_number", 0) or 0
                ),
            )
            for field in (getattr(item, "fields", ()) or ())
        ),
        icon_texture=str(getattr(item, "icon_texture", "")),
        icon_path=icon_path,
        icon_x=int(getattr(item, "icon_x", 0) or 0),
        icon_y=int(getattr(item, "icon_y", 0) or 0),
        icon_width=int(getattr(item, "icon_width", 1) or 1),
        icon_height=int(getattr(item, "icon_height", 1) or 1),
        line_number=int(getattr(item, "line_number", 0) or 0),
        name_text=str(getattr(item, "name_text", "") or ""),
        description_text=str(
            getattr(item, "description_text", "") or ""
        ),
        name_key=str(getattr(item, "name_key", "") or ""),
        description_key=str(
            getattr(item, "description_key", "") or ""
        ),
        name_source=_to_localization_string(
            getattr(item, "name_source", None)
        ),
        description_source=_to_localization_string(
            getattr(item, "description_source", None)
        ),
        text_sources={
            str(key): source
            for key, value in (
                getattr(item, "text_sources", None) or {}
            ).items()
            if (source := _to_localization_string(value)) is not None
        },
    )

def _to_section_graph_view(graph) -> SectionGraph:
    return SectionGraph(
        section_name=str(getattr(graph, "section_name", "")),
        root=_to_graph_node_view(getattr(graph, "root")),
        children=tuple(
            _to_graph_node_view(node)
            for node in (getattr(graph, "children", ()) or ())
        ),
        lost_children=tuple(
            _to_graph_node_view(node)
            for node in (getattr(graph, "lost_children", ()) or ())
        ),
    )


def _to_graph_node_view(node) -> GraphNode:
    matches = tuple(
        GraphMatch(
            edge_type=_edge_type(match),
            line_number=int(getattr(match, "line_number", 0) or 0),
            line_text=str(getattr(match, "line_text", "") or ""),
            related=getattr(match, "related", None),
            related_is_parent=bool(
                getattr(match, "related_is_parent", False)
            ),
        )
        for match in (getattr(node, "matches", ()) or ())
    )

    file_path = getattr(node, "file_path", None)
    if file_path is not None:
        file_path = Path(file_path)

    target_path = getattr(node, "target_path", None)
    if target_path is not None:
        target_path = Path(target_path)

    physical_path = getattr(node, "physical_path", None)
    if physical_path is not None:
        physical_path = Path(physical_path)

    related_file_path = getattr(node, "related_file_path", None)
    if related_file_path is not None:
        related_file_path = Path(related_file_path)

    related_line = getattr(node, "related_line", None)
    if related_line is not None:
        related_line = int(related_line)

    return GraphNode(
        display_name=str(getattr(node, "display_name", "")),
        file_path=file_path,
        first_match_line=int(getattr(node, "first_match_line", 0) or 0),
        matches=matches,
        raw_ref=getattr(node, "raw_ref", None),
        resolved=bool(getattr(node, "resolved", False)),
        target_path=target_path,
        physical_path=physical_path,
        materialized=bool(getattr(node, "materialized", False)),
        related_file_path=related_file_path,
        related_line=related_line,
        related_resolved=bool(
            getattr(node, "related_resolved", False)
        ),
    )


def _edge_type(match) -> str:
    edge_type = getattr(match, "edge_type", "")
    return str(getattr(edge_type, "value", edge_type))
