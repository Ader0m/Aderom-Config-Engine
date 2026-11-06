# -*- coding: utf-8 -*-
"""Адаптеры между SDK-контрактами и реальными core/ui.

Адаптеры — единственное место, где SDK знает о форме конкретных
сервисов приложения. Модули работают только с :class:`sdk.api.CoreApi`
и :class:`sdk.api.UiApi`.
"""

from __future__ import annotations

from pathlib import Path

from .api import (
    GraphMatch,
    GraphNode,
    ItemCatalog,
    ItemEntry,
    ItemField,
    ItemIcon,
    SectionGraph,
    TextureRegion,
    TreeSummary,
    XmlResolveResult,
)


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
            xml_files=int(getattr(forest, "total_xml_files", 0) or 0),
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

    # --- фоновая предсборка ------------------------------------------------

    def start_prebuild(self, forest, config_dir):
        return self._provider.start_prebuild(forest, config_dir)

    def prebuild_status(self):
        return self._provider.prebuild_status()

    def wait_prebuild(self, log=None):
        return self._provider.wait_prebuild(log=log)

    def stop_prebuild(self):
        return self._provider.stop_prebuild()

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

    # --- XML-резолв -------------------------------------------------------

    def resolve_xml(self, path, config_dir, log=None, dxml=True):
        raw = self._provider.resolve_xml(
            path,
            config_dir,
            log=log,
            dxml=dxml,
        )
        return XmlResolveResult(
            text=raw.text,
            virtual_name=raw.virtual_name,
            dxml_applied=bool(raw.dxml_applied),
            notes=tuple(raw.notes or ()),
            line_sources=tuple(getattr(raw, "line_sources", ()) or ()),
        )

    def resolve_xml_to_file(self, path, config_dir, log=None, dxml=True):
        return self._provider.resolve_xml_to_file(
            path,
            config_dir,
            log=log,
            dxml=dxml,
        )

    # --- предметы и иконки -----------------------------------------------

    def build_item_catalog(self, forest, log=None):
        raw = self._provider.build_item_catalog(forest, log=log)

        source_path = getattr(raw, "source_path", None)
        if source_path is not None:
            source_path = Path(source_path)

        items = []
        for item in (getattr(raw, "items", ()) or ()):
            icon_path = getattr(item, "icon_path", None)
            if icon_path is not None:
                icon_path = Path(icon_path)

            items.append(ItemEntry(
                section=str(getattr(item, "section", "")),
                fields=tuple(
                    ItemField(
                        key=str(getattr(field, "key", "")),
                        value=str(getattr(field, "value", "")),
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
            ))

        return ItemCatalog(
            source_path=source_path,
            items=tuple(items),
            warnings=tuple(getattr(raw, "warnings", ()) or ()),
            cache_key=str(getattr(raw, "cache_key", "") or ""),
        )

    def read_texture_region(self, path, x, y, width, height):
        raw = self._provider.read_texture_region(path, x, y, width, height)
        return TextureRegion(
            width=int(raw.width),
            height=int(raw.height),
            rgba=bytes(raw.rgba),
        )

    def load_item_icons(self, catalog, log=None):
        requests = []
        for entry in (getattr(catalog, "items", ()) or ()):
            if entry.icon_path is None:
                continue

            x, y, width, height = entry.icon_rect
            if x < 0 or y < 0 or width <= 0 or height <= 0:
                continue

            requests.append((
                entry.section,
                str(entry.icon_path),
                x,
                y,
                width,
                height,
            ))

        raw = self._provider.load_item_icons(
            getattr(catalog, "cache_key", "") or "",
            requests,
            log=log,
        )

        return tuple(
            ItemIcon(
                section=str(section),
                width=int(region.width),
                height=int(region.height),
                rgba=bytes(region.rgba),
            )
            for section, region in (raw or ())
        )

    # --- граф связей и внешние инструменты -------------------------------

    def build_section_graph(self, forest, section_name, vfs_mode=False,
                            log=None):
        graph = self._provider.build_section_graph(
            forest,
            section_name,
            vfs_mode=vfs_mode,
            log=log,
        )
        return _to_section_graph_view(graph)

    def open_external_editor(self, path):
        for name in ("open_external_editor", "open_notepad", "open_in_notepad"):
            fn = getattr(self._provider, name, None)
            if callable(fn):
                return fn(path)
        return False, "Внешний редактор не настроен."

    def open_with_shell(self, path):
        for name in ("open_with_shell", "open_shell", "open_path"):
            fn = getattr(self._provider, name, None)
            if callable(fn):
                return fn(path)
        return False, "Открытие средствами оболочки не настроено."


# ---------------------------------------------------------------- events


class EventApiAdapter:
    """Реализация :class:`sdk.api.EventApi` поверх шины событий."""

    _sdk_api_kind = "events"

    def __init__(self, bus):
        self._bus = bus

    @classmethod
    def wrap(cls, bus):
        if getattr(bus, _SDK_API_MARKER, None) == cls._sdk_api_kind:
            return bus
        return cls(bus)

    def subscribe(self, name, handler, priority=0):
        return self._bus.subscribe(name, handler, priority=priority)

    def unsubscribe(self, name, handler=None):
        return self._bus.unsubscribe(name, handler)

    def publish(self, name, payload=None):
        return self._bus.publish(name, payload)

    def dispatch(self, name, payload=None):
        return self._bus.dispatch(name, payload)

    def has(self, name):
        return self._bus.has(name)


# ---------------------------------------------------------------- commands


class CommandApiAdapter:
    """Реализация :class:`sdk.api.CommandApi` поверх шины команд."""

    _sdk_api_kind = "commands"

    def __init__(self, bus):
        self._bus = bus

    @classmethod
    def wrap(cls, bus):
        if getattr(bus, _SDK_API_MARKER, None) == cls._sdk_api_kind:
            return bus
        return cls(bus)

    def register(self, name, handler):
        return self._bus.register(name, handler)

    def unregister(self, name):
        return self._bus.unregister(name)

    def has(self, name):
        return self._bus.has(name)

    def execute(self, name, *args, **kwargs):
        return self._bus.execute(name, *args, **kwargs)


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

    def register_tree_context_action(self, label, handler, predicate=None):
        self._platform.register_tree_context_action(label, handler, predicate)

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

    def open_resolved_tab(self, tab_id, title, text, line_sources, root_path,
                          line_number=None):
        self._platform.open_resolved_tab(
            tab_id,
            title,
            text,
            line_sources,
            root_path,
            line_number,
        )

    def open_xml_resolved_tab(self, tab_id, title, text, root_path,
                              line_sources=(), line_number=None):
        self._platform.open_xml_resolved_tab(
            tab_id,
            title,
            text,
            root_path,
            line_sources,
            line_number,
        )

    def open_file_with_search(self, path, search_text):
        self._platform.commands.execute("ui.open_file_with_search", path, search_text)
        
    # --- уведомления и запросы --------------------------------------------

    def log(self, message):
        self._platform.log(message)

    def log_status(self, key, message):
        self._platform.log_status(key, message)

    def set_status(self, message, timeout=0):
        self._platform.set_status(message, timeout)

    def show_error(self, text):
        self._platform.show_error(text)

    def ask_directory(self, initial=""):
        return self._platform.ask_directory(initial)

    def ask_text(self, prompt, initial=""):
        return self._platform.ask_text(prompt, initial)

    def show_text(self, text, label, readonly=True, source_path=None):
        self._platform.show_text(
            text,
            label,
            readonly=readonly,
            source_path=source_path,
        )

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

    def theme_color(self, name, fallback=None):
        fn = getattr(self._platform, "theme_color", None)
        if callable(fn):
            return fn(name, fallback)
        return fallback

    # --- команды платформы ------------------------------------------------

    def open_raw(self, path):
        self._platform.commands.execute("ui.open_file_raw", path)

    def open_at_line(self, path, line_number):
        self._platform.commands.execute(
            "ui.open_file_at_line",
            path,
            int(line_number),
        )

    def open_raw_at_line(self, path, line_number):
        self._platform.commands.execute(
            "ui.open_file_raw_at_line",
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
        related_file_path=related_file_path,
        related_line=related_line,
        related_resolved=bool(
            getattr(node, "related_resolved", False)
        ),
    )


def _edge_type(match) -> str:
    edge_type = getattr(match, "edge_type", "")
    return str(getattr(edge_type, "value", edge_type))
