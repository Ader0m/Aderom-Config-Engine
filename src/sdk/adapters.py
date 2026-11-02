# -*- coding: utf-8 -*-
"""Адаптеры между SDK-контрактами и реальными core/ui.

Адаптеры — единственное место, где SDK знает о форме конкретных
сервисов приложения. Модули работают только с :class:`sdk.api.CoreApi`
и :class:`sdk.api.UiApi`.
"""

from __future__ import annotations

from pathlib import Path

from .api import GraphMatch, GraphNode, SectionGraph, TreeSummary


_SDK_API_MARKER = "_sdk_api_kind"


# ---------------------------------------------------------------- core


class CoreApiAdapter:
    """Реализация :class:`sdk.api.CoreApi` поверх фасада ядра."""

    _sdk_api_kind = "core"

    def __init__(self, provider):
        self._provider = provider

    @classmethod
    def wrap(cls, provider):
        if getattr(provider, _SDK_API_MARKER, None) == cls._sdk_api_kind:
            return provider
        return cls(provider)

    # --- конфигурация и дерево -------------------------------------------

    def detect_config_dir(self, candidates):
        return self._provider.detect_config_dir(candidates)

    def find_system_ltx(self, selected_dir):
        return self._provider.find_system_ltx(selected_dir)

    def resolve_scan_dir(self, config_dir):
        fn = getattr(self._provider, "resolve_scan_dir", None)
        if callable(fn):
            return fn(config_dir)

        config_dir = Path(config_dir)
        if (config_dir / "system.ltx").is_file():
            return config_dir

        system = self.find_system_ltx(config_dir)
        return Path(system).parent if system is not None else None

    def build_tree(self, scan_dir, system_root, log=None, clean=False):
        return self._provider.build_tree(
            scan_dir,
            system_root,
            log=log,
            clean=clean,
        )

    def tree_summary(self, forest):
        resolver_sections = getattr(forest, "resolver_sections", None)
        if resolver_sections is not None:
            try:
                resolver_sections = int(resolver_sections)
            except (TypeError, ValueError):
                pass

        return TreeSummary(
            roots=len(getattr(forest, "roots", ()) or ()),
            total_files=int(getattr(forest, "total_files", 0) or 0),
            system_tree_files=int(getattr(forest, "system_tree_files", 0) or 0),
            resolver_sections=resolver_sections,
        )

    def relative_path(self, forest, path):
        rel = getattr(self._provider, "relative_path", None)
        if callable(rel):
            return rel(forest, path)
        return self._provider.rel_path(forest, path)

    def patch_target(self, forest, path):
        parents = getattr(forest, "include_parents", {}).get(path) or ()
        if not parents:
            return None
        return sorted(parents, key=lambda p: str(p).lower())[0]

    def read_text(self, path):
        reader = getattr(self._provider, "read_text", None)
        if callable(reader):
            return reader(path)
        return Path(path).read_text(encoding="utf-8", errors="replace")

    # --- резолвер и сборка ------------------------------------------------

    def run_resolver(self, forest, config_dir, log=None):
        fn = getattr(self._provider, "run_resolver", None)
        if callable(fn):
            return fn(forest, config_dir, log=log)

        resolver = getattr(self._provider, "resolver", None)
        if resolver is not None:
            return resolver.run_pass(forest, config_dir, log=log)

        return self._provider.resolver_pass(forest, config_dir, log=log)

    def assemble_text(self, root_path, forest, config_dir, log=None):
        fn = getattr(self._provider, "assemble_text", None)
        if callable(fn):
            return fn(root_path, forest, config_dir, log=log)

        return self._provider.resolver.assemble_text(
            root_path,
            forest,
            config_dir,
            log=log,
        )

    def assemble_to_file(self, root_path, forest):
        fn = getattr(self._provider, "assemble_to_file", None)
        if callable(fn):
            return fn(root_path, forest)

        return self._provider.assembly.assemble_to_file(root_path, forest)

    def assemble_resolved(self, root_path, forest, config_dir, log=None):
        fn = getattr(self._provider, "assemble_resolved", None)
        if callable(fn):
            return fn(root_path, forest, config_dir, log=log)
        return self._provider.resolver.assemble_resolved(
            root_path, forest, config_dir, log=log,
        )

    # --- граф связей и внешние инструменты -------------------------------

    def build_section_graph(self, forest, section_name):
        graph = self._provider.build_section_graph(forest, section_name)
        return _to_section_graph_view(graph)

    def open_external_editor(self, path):
        for name in ("open_external_editor", "open_notepad", "open_in_notepad"):
            fn = getattr(self._provider, name, None)
            if callable(fn):
                return fn(path)
        return False, "Внешний редактор не настроен."


# ---------------------------------------------------------------- tasks


class TaskApiAdapter:
    """Реализация :class:`sdk.api.TaskApi` поверх сервиса фоновых задач."""

    _sdk_api_kind = "tasks"

    def __init__(self, service):
        self._service = service

    @classmethod
    def wrap(cls, service):
        if getattr(service, _SDK_API_MARKER, None) == cls._sdk_api_kind:
            return service
        return cls(service)

    def run(self, fn, on_done=None, on_log=None):
        return self._service.run(fn, on_done=on_done, on_log=on_log)


# ---------------------------------------------------------------- ui

class UiApiAdapter:
    """Реализация :class:`sdk.api.UiApi` поверх платформы UI."""

    _sdk_api_kind = "ui"

    def __init__(self, platform):
        self._platform = platform

    @classmethod
    def wrap(cls, platform):
        if getattr(platform, _SDK_API_MARKER, None) == cls._sdk_api_kind:
            return platform
        return cls(platform)

    # --- регистрация возможностей модуля ---------------------------------

    def register_menu(self, menu_title, label, callback, shortcut=None, action=None):
        self._platform.register_menu(
            menu_title,
            label,
            callback,
            shortcut,
            action,
        )

    def register_tree_context_action(self, label, handler):
        self._platform.register_tree_context_action(label, handler)

    def register_open_handler(self, handler):
        self._platform.register_open_handler(handler)

    def register_shortcut(self, key, callback, scope="global", action=None):
        self._platform.register_shortcut(
            key,
            callback,
            scope=scope,
            action=action,
        )

    def register_mouse_binding(
        self,
        button,
        modifiers,
        callback,
        scope="global",
        event="press",
        action=None,
    ):
        self._platform.register_mouse_binding(
            button,
            modifiers,
            callback,
            scope=scope,
            event=event,
            action=action,
        )

    def register_action_handler(self, action, handler, priority=0):
        return self._platform.register_action_handler(
            action,
            handler,
            priority,
        )

    def dispatch_action(self, action, payload=None):
        return self._platform.dispatch_action(action, payload)

    def open_resolved_tab(self, tab_id, title, text, line_sources, root_path):
        self._platform.open_resolved_tab(tab_id, title, text, line_sources, root_path)

    def open_file_with_search(self, path, search_text):
        self._platform.commands.execute("ui.open_file_with_search", path, search_text)
        
    # --- уведомления и запросы --------------------------------------------

    def log(self, message):
        self._platform.log(message)

    def set_status(self, message, timeout=0):
        self._platform.set_status(message, timeout)

    def show_error(self, text):
        self._platform.show_error(text)

    def ask_directory(self, initial=""):
        return self._platform.ask_directory(initial)

    def ask_text(self, prompt, initial=""):
        return self._platform.ask_text(prompt, initial)

    def show_text(self, text, label, readonly=True):
        self._platform.show_text(text, label, readonly=readonly)

    def show_module_window(self, key, window):
        self._platform.show_module_window(key, window)

    def close_module_window(self, key):
        self._platform.close_module_window(key)

    def open_editor_tab(self, tab_id, title, widget):
        self._platform.open_editor_tab(tab_id, title, widget)

    def close_editor_tab(self, tab_id):
        self._platform.close_editor_tab(tab_id)

    def copy_to_clipboard(self, text):
        self._platform.clipboard_set(text)

    # --- команды платформы ------------------------------------------------

    def open_raw(self, path):
        self._platform.commands.execute("ui.open_file_raw", path)

    def open_at_line(self, path, line_number):
        self._platform.commands.execute(
            "ui.open_file_at_line",
            path,
            int(line_number),
        )

    def undo(self):
        self._platform.commands.execute("ui.editor_undo")

    def redo(self):
        self._platform.commands.execute("ui.editor_redo")

    def find_in_editor(self):
        self._platform.commands.execute("ui.editor_find")

    def sort_file_tree_by_files_desc(self):
        window = getattr(self._platform, "window", None)
        tree = getattr(window, "file_tree", None)
        fn = getattr(tree, "sort_by_files_desc", None)
        if callable(fn):
            fn()


# ---------------------------------------------------------------- преобразование


def _to_section_graph_view(graph) -> SectionGraph:
    return SectionGraph(
        section_name=str(getattr(graph, "section_name", "")),
        root=_to_graph_node_view(getattr(graph, "root")),
        children=tuple(
            _to_graph_node_view(node)
            for node in (getattr(graph, "children", ()) or ())
        ),
    )


def _to_graph_node_view(node) -> GraphNode:
    matches = tuple(
        GraphMatch(
            edge_type=_edge_type(match),
            line_number=int(getattr(match, "line_number", 0) or 0),
            line_text=str(getattr(match, "line_text", "") or ""),
        )
        for match in (getattr(node, "matches", ()) or ())
    )

    file_path = getattr(node, "file_path", None)
    if file_path is not None:
        file_path = Path(file_path)

    return GraphNode(
        display_name=str(getattr(node, "display_name", "")),
        file_path=file_path,
        first_match_line=int(getattr(node, "first_match_line", 0) or 0),
        matches=matches,
        raw_ref=getattr(node, "raw_ref", None),
    )


def _edge_type(match) -> str:
    edge_type = getattr(match, "edge_type", "")
    return str(getattr(edge_type, "value", edge_type))
