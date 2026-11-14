# -*- coding: utf-8 -*-
"""РЎРІСЏР·Рё СЃРµРєС†РёРё: .ogg-С„Р°Р№Р»С‹ РёР· snd_*-РїРѕР»РµР№ РґР»СЏ РѕР±С‰РµРіРѕ РіСЂР°С„Р°.

Р’СЃСЏ Р·РІСѓРєРѕРІР°СЏ СЃРµРјР°РЅС‚РёРєР° (РєР°РєРёРµ РїРѕР»СЏ СЃС‡РёС‚Р°СЋС‚СЃСЏ Р·РІСѓРєРѕРІС‹РјРё, РєР°Рє РёСЃРєР°С‚СЊ
.ogg, РєР°Рє РѕС‚Р»РёС‡Р°С‚СЊ РїРѕС‚РµСЂСЏРЅРЅС‹Рµ СЃСЃС‹Р»РєРё) Р¶РёРІС‘С‚ Р·РґРµСЃСЊ. Р‘Р°Р·РѕРІС‹Р№ РјРѕРґСѓР»СЊ
С‚РѕР»СЊРєРѕ СЃРєР»Р°РґС‹РІР°РµС‚ РїРѕР»СѓС‡РµРЅРЅС‹Рµ СѓР·Р»С‹ РІ РѕР±С‰РёР№ РіСЂР°С„.
"""

from __future__ import annotations

from pathlib import Path

from sdk import (
    GraphEdgeStyle,
    GraphMatch,
    GraphNode,
    SectionGraphContribution,
    path_key as sdk_path_key,
)

from .sections import extract_sound_references


class OggSectionGraphContributor:
    """Р”РѕРїРѕР»РЅСЏРµС‚ РіСЂР°С„ СЃРІСЏР·РµР№ СЃРµРєС†РёРё РЅР°Р№РґРµРЅРЅС‹РјРё .ogg-С„Р°Р№Р»Р°РјРё."""

    EDGE_TYPE = "sound_reference"
    EDGE_STYLES = {
        EDGE_TYPE: GraphEdgeStyle("Р—РІСѓРє", "graph_sound"),
    }

    def __init__(self, ctx):
        self.ctx = ctx

    def open_node(self, node):
        """РћС‚РєСЂС‹С‚СЊ Р·РІСѓРєРѕРІРѕР№ СѓР·РµР» СЃРёР»Р°РјРё OGG-РјРѕРґСѓР»СЏ."""
        target = (
            getattr(node, "target_path", None)
            or getattr(node, "physical_path", None)
        )
        if target is None:
            self.ctx.ui.show_error(
                "Р—РІСѓРєРѕРІРѕР№ С„Р°Р№Р» РЅРµ РЅР°Р№РґРµРЅ: %s"
                % (
                    getattr(node, "raw_ref", None)
                    or getattr(node, "display_name", "")
                )
            )
            return
        self.ctx.ui.open_file(target)

    def __call__(self, request):
        graph = getattr(request, "graph", None)
        root = getattr(graph, "root", None)
        root_path = getattr(root, "file_path", None)
        if root_path is None:
            return SectionGraphContribution(edge_styles=self.EDGE_STYLES)

        forest = request.forest
        services = self.ctx.services
        config_dir = services.forest_scan_dir(forest)
        game_root = services.game_root(forest)
        if config_dir is None or game_root is None:
            return SectionGraphContribution(edge_styles=self.EDGE_STYLES)

        sounds_root = Path(game_root) / "sounds"
        if not sounds_root.is_dir():
            return SectionGraphContribution(edge_styles=self.EDGE_STYLES)

        root_path = Path(root_path)
        found_refs = self._resolved_references(
            services,
            root_path,
            forest,
            config_dir,
            request,
            sounds_root,
        )
        found = self._make_nodes(found_refs, resolved=True)

        lost = ()
        if getattr(request, "vfs_mode", False):
            found_paths = {
                self._path_key(reference.path)
                for reference in found_refs
            }
            vfs_refs = self._vfs_references(
                services,
                graph,
                request.section_name,
                sounds_root,
            )
            lost_refs = [
                reference for reference in vfs_refs
                if self._path_key(reference.path) not in found_paths
            ]
            lost = self._make_nodes(lost_refs, resolved=False)
            # Р›РµРІС‹Р№ СЃРїРёСЃРѕРє СѓР¶Рµ Р·Р°РјРѕСЂРѕР¶РµРЅ; РІ VFS-СЂРµР¶РёРјРµ РјРѕРґСѓР»СЊ
            # РґРѕР±Р°РІР»СЏРµС‚ С‚РѕР»СЊРєРѕ РїРѕС‚РµСЂСЏРЅРЅС‹Рµ Р·РІСѓРєРё РІ РїСЂР°РІСѓСЋ РїР°РЅРµР»СЊ.
            found = ()

        return SectionGraphContribution(
            children=found,
            lost_children=lost,
            edge_styles=self.EDGE_STYLES,
            openers={self.EDGE_TYPE: self.open_node},
        )

    def _resolved_references(self, services, root_path, forest,
                             config_dir, request, sounds_root):
        try:
            text, _line_sources = services.assemble_resolved(
                root_path,
                forest,
                config_dir,
                overlay=getattr(request, "overlay", None),
            )
        except Exception:
            return []
        return extract_sound_references(
            text,
            request.section_name,
            sounds_root,
            source_path=root_path,
        )

    def _vfs_references(self, services, graph, section_name, sounds_root):
        """РЎСЃС‹Р»РєРё РёР· РІСЃРµС… VFS-С„Р°Р№Р»РѕРІ, РіРґРµ РІСЃС‚СЂРµС‡Р°РµС‚СЃСЏ СЃРµРєС†РёСЏ."""
        paths = []
        root = getattr(graph, "root", None)
        if root is not None and root.file_path is not None:
            paths.append(Path(root.file_path))

        for node in (
            tuple(getattr(graph, "children", ()) or ())
            + tuple(getattr(graph, "lost_children", ()) or ())
        ):
            file_path = getattr(node, "file_path", None)
            if file_path is not None:
                path = Path(file_path)
                if path.suffix.lower() == ".ltx":
                    paths.append(path)

        seen = set()
        references = []
        for path in paths:
            key = self._path_key(path)
            if key in seen:
                continue
            seen.add(key)
            try:
                text = services.read_text(path)
            except Exception:
                try:
                    text = path.read_text(
                        encoding="utf-8",
                        errors="replace",
                    )
                except Exception:
                    continue
            references.extend(
                extract_sound_references(
                    text,
                    section_name,
                    sounds_root,
                    source_path=path,
                )
            )
        return references

    def _make_nodes(self, references, resolved):
        grouped = {}
        for reference in references:
            source = reference.source_path
            key = (
                self._path_key(source) if source is not None else "",
                self._path_key(reference.path),
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
                    file_path=first.source_path,
                    first_match_line=min(
                        item.line_number for item in items
                    ),
                    matches=matches,
                    raw_ref=first.value,
                    resolved=resolved,
                    target_path=first.path,
                    physical_path=first.path,
                )
            )

        nodes.sort(
            key=lambda node: (
                str(node.file_path).lower(),
                str(node.target_path).lower(),
            )
        )
        return tuple(nodes)

    @staticmethod
    def _path_key(path) -> str:
        if path is None:
            return ""
        return sdk_path_key(path)
