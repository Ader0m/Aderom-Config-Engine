# -*- coding: utf-8 -*-
"""Граф связей секции."""

from __future__ import annotations

from pathlib import Path

from core.tools.cache import project_overlay_signature


class GraphServices:
    """Построение графа секции поверх resolver/XML-провайдеров."""

    def __init__(self, graph_builder, resolver, xml):
        self.graph_builder = graph_builder
        self.resolver = resolver
        self.xml = xml

    def build_section_graph(
        self,
        forest,
        section_name: str,
        vfs_mode: bool = False,
        log=None,
        overlay=None,
    ):
        """Построить граф связей для секции."""
        reported = {"error": False}

        config_dir = Path(getattr(forest, "scan_dir", "") or "")

        def resolved_provider(root_path):
            try:
                text, line_sources = self.resolver.assemble_resolved(
                    root_path,
                    forest,
                    config_dir,
                    log=None,
                    overlay=overlay,
                )
                return text, line_sources
            except Exception as exc:
                if log and not reported["error"]:
                    log("resolved graph: резолвер недоступен: %s" % exc)
                    reported["error"] = True
                return None

        def xml_provider(xml_path):
            try:
                result = self.xml.resolve_output(
                    xml_path,
                    config_dir,
                    log=None,
                    overlay=overlay,
                )
                return result.text, result.line_sources
            except Exception as exc:
                if log and not reported["error"]:
                    log("resolved xml: XML-резолвер недоступен: %s" % exc)
                    reported["error"] = True
                return None

        forest_snapshot = str(getattr(forest, "cache_id", "") or "")
        snapshot_id = ""
        if forest_snapshot:
            snapshot_id = "%s|%s" % (
                forest_snapshot,
                project_overlay_signature(overlay),
            )

        try:
            return self.graph_builder.build_for_section(
                forest,
                section_name,
                vfs_mode=vfs_mode,
                resolved_provider=resolved_provider,
                xml_provider=xml_provider,
                log=log,
                snapshot_id=snapshot_id,
                provider_id="core",
            )
        finally:
            if log:
                if hasattr(self.resolver, "cache_stats"):
                    stats = self.resolver.cache_stats()
                    log(
                        "resolve cache: entries=%d, chars=%d, hits=%d, "
                        "disk_hits=%d, misses=%d"
                        % (
                            stats.get("entries", 0),
                            stats.get("chars", 0),
                            stats.get("hits", 0),
                            stats.get("disk_hits", 0),
                            stats.get("misses", 0),
                        )
                    )
                if hasattr(self.xml, "cache_stats"):
                    stats = self.xml.cache_stats()
                    log(
                        "xml cache: entries=%d, chars=%d, hits=%d, "
                        "disk_hits=%d, misses=%d"
                        % (
                            stats.get("entries", 0),
                            stats.get("chars", 0),
                            stats.get("hits", 0),
                            stats.get("disk_hits", 0),
                            stats.get("misses", 0),
                        )
                    )

    def clear_cache(self):
        if self.graph_builder is not None:
            self.graph_builder.clear_cache()


__all__ = ["GraphServices"]
