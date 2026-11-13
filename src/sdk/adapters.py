# -*- coding: utf-8 -*-
"""Адаптеры между SDK-контрактами и реальными core/ui.

Адаптеры — единственное место, где SDK знает о форме конкретных
сервисов приложения. Модули работают только с :class:`sdk.api.CoreApi`
и :class:`sdk.api.UiApi`.
"""

from __future__ import annotations

from pathlib import Path

from .api import (
    AssetDraftInfo,
    FileView,
    GraphMatch,
    ProjectAssetType,
    GraphNode,
    GraphEdgeStyle,
    ItemCatalog,
    ItemEntry,
    ItemField,
    ItemIcon,
    ItemSection,
    ProjectSetting,
    ProjectSettingChoice,
    SectionGraph,
    SectionGraphContribution,
    SectionGraphRequest,
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
        self._section_graph_contributors = self._shared_contributors()

    def _shared_contributors(self):
        """Один реестр на провайдера, даже если адаптеров несколько.

        ``ModuleContext`` создаёт отдельный адаптер для каждого модуля,
        поэтому хранить поставщиков в самом адаптере нельзя.
        """
        owner = self._provider
        if isinstance(owner, CoreApiAdapter):
            return owner._section_graph_contributors

        registry = getattr(
            owner,
            "_sdk_section_graph_contributors",
            None,
        )
        if registry is None:
            registry = []
            try:
                setattr(
                    owner,
                    "_sdk_section_graph_contributors",
                    registry,
                )
            except Exception:
                pass
        return registry

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

    def build_tree(self, scan_dir, system_root, log=None, clean=False,
                   overlay=None):
        return self._provider.build_tree(
            scan_dir,
            system_root,
            log=log,
            clean=clean,
            overlay=overlay,
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

    def forest_scan_dir(self, forest):
        value = getattr(forest, "scan_dir", None)
        return Path(value) if value else None

    def forest_system_root(self, forest):
        value = getattr(forest, "system_root", None)
        return Path(value) if value else None

    def game_root(self, forest):
        fn = getattr(self._provider, "forest_game_root", None)
        if callable(fn):
            return fn(forest)

        value = getattr(forest, "scan_dir", None)
        if not value:
            return None
        scan_dir = Path(value)
        if scan_dir.name.lower() == "configs":
            return scan_dir.parent
        if (scan_dir / "gamedata" / "configs").is_dir():
            return scan_dir / "gamedata"
        if (scan_dir / "configs").is_dir():
            return scan_dir
        return scan_dir.parent

    def add_virtual_file(self, forest, path, text):
        return self._provider.add_virtual_file(forest, path, text)

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

    def start_prebuild(self, forest, config_dir, overlay=None):
        return self._provider.start_prebuild(
            forest,
            config_dir,
            overlay=overlay,
        )

    def ensure_prebuild(self, forest=None, config_dir=None, overlay=None):
        fn = getattr(self._provider, "ensure_prebuild", None)
        if callable(fn):
            return fn(
                forest,
                config_dir,
                overlay=overlay,
            )
        if forest is not None and config_dir is not None:
            self._provider.start_prebuild(
                forest,
                config_dir,
                overlay=overlay,
            )
            return True
        return False

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

    def assemble_text(self, root_path, forest, config_dir, log=None,
                      overlay=None):
        fn = getattr(self._provider, "assemble_text", None)
        if callable(fn):
            return fn(
                root_path,
                forest,
                config_dir,
                log=log,
                overlay=overlay,
            )

        return self._provider.resolver.assemble_text(
            root_path,
            forest,
            config_dir,
            log=log,
            overlay=overlay,
        )

    def assemble_to_file(self, root_path, forest):
        fn = getattr(self._provider, "assemble_to_file", None)
        if callable(fn):
            return fn(root_path, forest)

        return self._provider.assembly.assemble_to_file(root_path, forest)

    def assemble_resolved(self, root_path, forest, config_dir, log=None,
                          overlay=None):
        fn = getattr(self._provider, "assemble_resolved", None)
        if callable(fn):
            return fn(
                root_path,
                forest,
                config_dir,
                log=log,
                overlay=overlay,
            )
        return self._provider.resolver.assemble_resolved(
            root_path,
            forest,
            config_dir,
            log=log,
            overlay=overlay,
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

    def item_catalog_root(self, forest):
        raw = self._provider.item_catalog_root(forest)
        return Path(raw) if raw is not None else None

    def build_item_catalog(
        self,
        forest,
        log=None,
        resolved_text=None,
        reuse_snapshot_cache=False,
    ):
        raw = self._provider.build_item_catalog(
            forest,
            log=log,
            resolved_text=resolved_text,
            reuse_snapshot_cache=reuse_snapshot_cache,
        )

        source_path = getattr(raw, "source_path", None)
        if source_path is not None:
            source_path = Path(source_path)

        items = []
        for item in (getattr(raw, "items", ()) or ()):
            items.append(self._item_entry(item))

        sections = tuple(
            ItemSection(
                name=str(getattr(section, "name", "")),
                fields=tuple(
                    str(field)
                    for field in (getattr(section, "fields", ()) or ())
                ),
                parents=tuple(
                    str(parent)
                    for parent in (getattr(section, "parents", ()) or ())
                ),
            )
            for section in (getattr(raw, "sections", ()) or ())
        )

        return ItemCatalog(
            source_path=source_path,
            items=tuple(items),
            warnings=tuple(getattr(raw, "warnings", ()) or ()),
            cache_key=str(getattr(raw, "cache_key", "") or ""),
            sections=sections,
            parent_names=tuple(
                str(name)
                for name in (getattr(raw, "parent_names", ()) or ())
            ),
        )

    @staticmethod
    def _item_entry(item) -> ItemEntry:
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
        )

    def build_item_entry(self, forest, resolved_text, section, log=None):
        raw = self._provider.build_item_entry(
            forest,
            resolved_text,
            section,
            log=log,
        )
        if raw is None:
            return None
        return self._item_entry(raw)

    def build_item_entry_from_block(
        self,
        forest,
        block_text,
        section,
        offset=0,
        log=None,
    ):
        raw = self._provider.build_item_entry_from_block(
            forest,
            block_text,
            section,
            offset=offset,
            log=log,
        )
        if raw is None:
            return None
        return self._item_entry(raw)

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
                            log=None, overlay=None):
        graph = self._provider.build_section_graph(
            forest,
            section_name,
            vfs_mode=vfs_mode,
            log=log,
            overlay=overlay,
        )
        return _to_section_graph_view(graph)

    def register_section_graph_contributor(self, contributor):
        if not callable(contributor):
            raise TypeError("contributor должен быть вызываемым")
        if contributor not in self._section_graph_contributors:
            self._section_graph_contributors.append(contributor)

    def unregister_section_graph_contributor(self, contributor):
        try:
            self._section_graph_contributors.remove(contributor)
        except ValueError:
            return False
        return True

    def collect_section_graph_contribution(self, request, log=None):
        children = []
        lost_children = []
        edge_styles = {}
        openers = {}
        for contributor in tuple(self._section_graph_contributors):
            try:
                result = contributor(request)
            except Exception as exc:
                if log is not None:
                    log("section graph contributor: %s" % exc)
                continue
            if not result:
                continue

            if isinstance(result, SectionGraphContribution):
                children.extend(result.children)
                lost_children.extend(result.lost_children)
                edge_styles.update(result.edge_styles)
                openers.update(result.openers)
                continue

            for node in result:
                if isinstance(node, GraphNode):
                    children.append(node)

        return SectionGraphContribution(
            children=tuple(children),
            lost_children=tuple(lost_children),
            edge_styles=edge_styles,
            openers=openers,
        )

    def collect_section_graph_nodes(self, request, log=None):
        return self.collect_section_graph_contribution(
            request,
            log=log,
        ).children

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

    def is_project_active(self):
        return self._platform.is_project_active()

    def project_name(self):
        fn = getattr(self._platform, "project_name", None)
        if callable(fn):
            return fn()
        return ""

    def project_overlay(self):
        fn = getattr(self._platform, "project_overlay", None)
        if callable(fn):
            return fn()
        return None

    def read_project_text(self, path):
        fn = getattr(self._platform, "document_text", None)
        if callable(fn):
            return fn(path)
        return ""

    def write_project_text(self, path, text):
        return self._platform.write_project_text(path, text)

    def write_project_texts(self, files):
        return self._platform.write_project_texts(files)

    def register_project_asset_type(self, asset_type):
        return self._platform.register_project_asset_type(asset_type)

    def unregister_project_asset_type(self, key):
        return self._platform.unregister_project_asset_type(key)

    def project_asset_types(self):
        result = []
        for item in self._platform.project_asset_types():
            result.append(
                ProjectAssetType(
                    key=str(getattr(item, "key", "") or ""),
                    label=str(getattr(item, "label", "") or ""),
                    extensions=tuple(
                        str(value) for value in getattr(
                            item,
                            "extensions",
                            (),
                        )
                    ),
                    vfs_root=str(getattr(item, "vfs_root", "") or ""),
                )
            )
        return tuple(result)

    def register_project_setting(self, setting):
        fn = getattr(self._platform, "register_project_setting", None)
        if not callable(fn):
            return False
        return bool(fn(setting))

    def unregister_project_setting(self, key):
        fn = getattr(self._platform, "unregister_project_setting", None)
        if not callable(fn):
            return False
        return bool(fn(key))

    def project_settings(self):
        fn = getattr(
            self._platform,
            "project_setting_definitions",
            None,
        )
        if not callable(fn):
            return ()
        result = []
        for item in fn():
            result.append(
                ProjectSetting(
                    key=str(getattr(item, "key", "") or ""),
                    label=str(getattr(item, "label", "") or ""),
                    kind=str(getattr(item, "kind", "") or "choice"),
                    default=getattr(item, "default", None),
                    choices=tuple(
                        ProjectSettingChoice(
                            value=str(
                                getattr(choice, "value", choice) or ""
                            ),
                            label=str(
                                getattr(choice, "label", "")
                                or getattr(choice, "value", choice)
                                or ""
                            ),
                        )
                        for choice in getattr(item, "choices", ()) or ()
                    ),
                    description=str(
                        getattr(item, "description", "") or ""
                    ),
                )
            )
        return tuple(result)

    def project_setting(self, key, default=None):
        fn = getattr(self._platform, "project_setting", None)
        if not callable(fn):
            return default
        return fn(key, default)

    def set_project_setting(self, key, value):
        fn = getattr(self._platform, "set_project_setting", None)
        if not callable(fn):
            return False
        return bool(fn(key, value))

    def register_file_reader(self, reader):
        return self._platform.register_file_reader(reader)

    def read_file_view(self, path, data=None):
        view = self._platform.read_file_view(path, data)
        if view is None:
            return None
        return FileView(
            text=str(getattr(view, "text", "") or ""),
            label=str(getattr(view, "label", "") or ""),
            details=tuple(
                str(value) for value in getattr(view, "details", ())
            ),
        )

    def read_project_asset(self, path):
        return self._platform.read_project_asset(path)

    def update_project_asset(self, path, data):
        return self._platform.update_project_asset(path, data)

    def save_project_asset(self, path, data=None):
        return self._platform.save_project_asset(path, data)

    def project_asset_drafts(self):
        result = []
        for draft in self._platform.project_asset_drafts():
            result.append(
                AssetDraftInfo(
                    path=Path(draft.path),
                    is_new=bool(getattr(draft, "is_new", False)),
                    changed=bool(getattr(draft, "changed", False)),
                    dirty=bool(getattr(draft, "dirty", False)),
                    size=int(getattr(draft, "size", 0) or 0),
                )
            )
        return tuple(result)

    def set_editor_tab_dirty(self, tab_id, dirty):
        return self._platform.set_editor_tab_dirty(tab_id, dirty)

    def resolved_draft(self, tab_id):
        return self._platform.resolved_draft(tab_id)

    def has_resolved_document(self, tab_id):
        return self._platform.has_resolved_document(tab_id)

    def resolved_document_title(self, tab_id):
        return self._platform.resolved_document_title(tab_id)

    def apply_resolved_line_edit(self, tab_id, action, line_number, text=""):
        return self._platform.apply_resolved_line_edit(
            tab_id,
            action,
            line_number,
            text,
        )

    def apply_resolved_field_edit(self, tab_id, section, key, value):
        return self._platform.apply_resolved_field_edit(
            tab_id,
            section,
            key,
            value,
        )

    def rename_resolved_field(
        self,
        tab_id,
        section,
        old_key,
        new_key,
        value,
    ):
        return self._platform.rename_resolved_field(
            tab_id,
            section,
            old_key,
            new_key,
            value,
        )

    def undo_resolved_document(self, tab_id):
        return self._platform.undo_resolved_document(tab_id)

    def redo_resolved_document(self, tab_id):
        return self._platform.redo_resolved_document(tab_id)

    def resolved_section_at_line(self, tab_id, line_number):
        return self._platform.resolved_section_at_line(tab_id, line_number)

    def item_entry(self, tab_id, section):
        return self._platform.item_entry(tab_id, section)

    def retain_resolved_document(self, tab_id):
        return self._platform.retain_resolved_document(tab_id)

    def release_resolved_document(self, tab_id):
        return self._platform.release_resolved_document(tab_id)

    def open_item_card_at_cursor(self):
        return self._platform.open_item_card_at_cursor()

    def update_resolved_draft(
        self,
        tab_id,
        root_path,
        title,
        original_text,
        current_text,
        line_sources=(),
        line_edit=None,
    ):
        return self._platform.update_resolved_draft(
            tab_id,
            root_path,
            title,
            original_text,
            current_text,
            line_sources,
            line_edit=line_edit,
        )

    def sync_resolved_tab(self, tab_id, text, line_sources, root_path):
        return self._platform.sync_resolved_tab(
            tab_id,
            text,
            line_sources,
            root_path,
        )

    def save_project_now(self):
        return self._platform.save_project_now()

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

    def ensure_resolved_document(self, tab_id, root_path, title="", text="",
                                 line_sources=()):
        return self._platform.ensure_resolved_document(
            tab_id,
            root_path,
            title,
            text,
            line_sources,
        )

    def open_resolved_document(self, tab_id, line_number=None):
        return self._platform.open_resolved_document(
            tab_id,
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

    def show_text(self, text, label, readonly=True, source_path=None,
                  line_number=None, syntax=True, notice=None):
        self._platform.show_text(
            text,
            label,
            readonly=readonly,
            source_path=source_path,
            line_number=line_number,
            syntax=syntax,
            notice=notice,
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

    def open_file(self, path):
        self._platform.commands.execute("ui.open_file", path)

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
        related_file_path=related_file_path,
        related_line=related_line,
        related_resolved=bool(
            getattr(node, "related_resolved", False)
        ),
    )


def _edge_type(match) -> str:
    edge_type = getattr(match, "edge_type", "")
    return str(getattr(edge_type, "value", edge_type))
