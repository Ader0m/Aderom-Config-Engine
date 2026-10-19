# -*- coding: utf-8 -*-
"""
CoreServices — дозированный фасад над сервисами ядра.

Композиционный корень создаёт один экземпляр и передаёт его:
- платформе UI (для отображения данных);
- модулям через SDK-контекст (ctx.services).

Модули и UI не импортируют внутренние модули ядра напрямую.
"""
import os
from pathlib import Path

from .external import open_in_notepad_pp
from .file_cache import FileCacheSystem
from .fs import (
    build_search_bases,
    build_source_map,
    find_resolver_script,
    find_system_ltx,
)
from .resolver_adapter import LtxResolverAdapter
from .tree_builder import TreeBuilder
from .tree_query_service import TreeQueryService, search_nodes, sort_nodes
from .fs import detect_config_dir

from .reference_graph import (
    ReferenceGraphBuilder,
    GraphDiagnostic,
    GraphSnapshot,
    create_graph_input,
)

class CoreServices:
    def __init__(self, generated_dir):
        self.cache = FileCacheSystem()
        self.tree_builder = TreeBuilder(cache=self.cache)
        self.tree_query = TreeQueryService()
        self.generated_dir = Path(generated_dir)
        self._adapter = None
        self._graph_builder = None
        self._current_resolver = None
        self._graph_forest = None

    # -------------------------------------------------- кэш
    def invalidate_cache(self):
        self.cache.clear()
        self._graph_builder = None
        self._current_resolver = None
    # -------------------------------------------------- дерево
    def find_system_ltx(self, sel):
        return find_system_ltx(sel)

    def build_tree(self, scan_dir, system_root, log=None, clean=False):
        if clean:
            self.invalidate_cache()
        return self.tree_builder.build(
            scan_dir=scan_dir,
            system_root=system_root,
            log=log,
            use_cache=True,
        )

    def sort_nodes(self, forest, nodes, col="path", reverse=False):
        return sort_nodes(forest, nodes, col=col, reverse=reverse)

    def search_nodes(self, forest, text):
        return search_nodes(forest, text)

    def rel_path(self, forest, path):
        return forest.rel_paths.get(path, str(path))

    # -------------------------------------------------- резолвер
    def _ensure_adapter(self, forest, config_dir):
        if self._adapter is not None:
            return self._adapter

        bases = build_search_bases(forest.scan_dir, config_dir)
        script = find_resolver_script(bases)
        if script is None:
            return None

        self._adapter = LtxResolverAdapter(script, cache=self.cache)
        return self._adapter

    def resolver_pass(self, forest, config_dir, log=None):
        adapter = self._ensure_adapter(forest, config_dir)

        if adapter is None:
            if log:
                log("Resolver4.py не найден — проход пропущен")
            return None

        if log:
            log("Resolver4: %s" % adapter.resolver_script)

        count = adapter.pass_only(forest.system_root)
        forest.resolver_sections = count

        if log:
            log("Resolver4 pass: секций %s" % count)

        return count

    # -------------------------------------------------- граф связей
    def _resolver_instance(self, forest, config_dir=None):
        if self._ensure_adapter(forest, config_dir) is None:
            return None

        if self._current_resolver is None:
            r = self._adapter._make(forest.system_root)
            r.load()
            self._current_resolver = r

        return self._current_resolver

    def _make_graph_input(self):
        from .reference_graph import create_graph_input

        return create_graph_input(
            forest=self._graph_forest,
            resolver=self._current_resolver,
        )

    def build_reference_graph(self, forest, section_name, config_dir=None):
        from .reference_graph import GraphDiagnostic, ReferenceGraphBuilder

        resolver = self._resolver_instance(forest, config_dir)
        if resolver is None:
            return GraphDiagnostic(
                section=section_name,
                message="Resolver4 не загружен — граф недоступен",
            )

        self._graph_forest = forest

        if self._graph_builder is None:
            self._graph_builder = ReferenceGraphBuilder(
                input_factory=self._make_graph_input,
                cache=self.cache,
            )

        return self._graph_builder.get_section_graph(section_name)

    def invalidate_reference_graph(self):
        if self._graph_builder is not None:
            self._graph_builder.invalidate()

    # -------------------------------------------------- сборка и внешние инструменты
    def source_map(self, forest):
        return build_source_map(forest)

    def assemble_to_file(self, root_path, source_map):
        adapter = self._adapter
        if adapter is None:
            raise RuntimeError("Резолвер не загружен")
        return adapter.assemble_to_file(root_path, self.generated_dir, source_map)

    def open_notepad(self, path):
        return open_in_notepad_pp(path)

    def detect_config_dir(self, candidates):
        return detect_config_dir(candidates)

    def assemble_text(self, root_path, forest, config_dir, log=None):
        adapter = self._ensure_adapter(forest, config_dir)
        if adapter is None:
            raise RuntimeError("Resolver4.py не найден")
        sm = self.source_map(forest)      # было: build_source_map(forest)
        return adapter.assemble_text(root_path, sm)