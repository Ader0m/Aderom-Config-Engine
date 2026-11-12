# -*- coding: utf-8 -*-
"""Связи секции: .ogg-файлы из snd_*-полей для общего графа."""

from __future__ import annotations

import os
from pathlib import Path

from sdk import GraphMatch, GraphNode

from .sections import extract_sound_references


class OggSectionGraphContributor:
    """Дополняет граф связей секции найденными .ogg-файлами."""

    EDGE_TYPE = "sound_reference"

    def __init__(self, ctx):
        self.ctx = ctx

    def __call__(self, request):
        # VFS-режим показывает сырые связи; звуковые ссылки корректно
        # разрешаются только по итоговому (resolved) тексту.
        if getattr(request, "vfs_mode", False):
            return ()

        graph = getattr(request, "graph", None)
        root = getattr(graph, "root", None)
        root_path = getattr(root, "file_path", None)
        if root_path is None:
            return ()

        forest = request.forest
        services = self.ctx.services
        config_dir = services.forest_scan_dir(forest)
        game_root = services.game_root(forest)
        if config_dir is None or game_root is None:
            return ()

        sounds_root = Path(game_root) / "sounds"
        if not sounds_root.is_dir():
            return ()

        root_path = Path(root_path)
        try:
            text, _line_sources = services.assemble_resolved(
                root_path,
                forest,
                config_dir,
                overlay=getattr(request, "overlay", None),
            )
        except Exception:
            return ()

        references = extract_sound_references(
            text,
            request.section_name,
            sounds_root,
        )
        if not references:
            return ()

        grouped = {}
        for reference in references:
            key = os.path.normcase(
                os.path.abspath(str(reference.path))
            )
            grouped.setdefault(key, []).append(reference)

        nodes = []
        for items in grouped.values():
            first = items[0]
            matches = tuple(
                GraphMatch(
                    edge_type=self.EDGE_TYPE,
                    line_number=item.line_number,
                    line_text=item.line_text,
                    related=item.field,
                )
                for item in items
            )
            nodes.append(
                GraphNode(
                    display_name=first.path.name,
                    file_path=root_path,
                    first_match_line=min(
                        item.line_number for item in items
                    ),
                    matches=matches,
                    raw_ref=first.value,
                    resolved=True,
                    target_path=first.path,
                    physical_path=first.path,
                )
            )

        nodes.sort(key=lambda node: str(node.target_path).lower())
        return tuple(nodes)
