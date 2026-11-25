# -*- coding: utf-8 -*-
"""CoreApiAdapter: сервисы ядра без доменных методов предметов."""

from __future__ import annotations

import threading
import weakref

from sdk.contracts.forest import forest_ref, unwrap_forest
from sdk.contracts.models import (
    GraphNode,
    ProjectOverlay,
    SectionGraphContribution,
    SectionGraphRequest,
)
from sdk.features.documents.models import LineSource, XmlResolveResult  # noqa: F401
from sdk.features.patches.models import PatchTargetInfo
from sdk.kernel.registrations import RegistrationHandle, remove_identity

from .common import _SDK_API_MARKER, _to_patch_target_info, _to_section_graph_view
from ..tree_summary import summarize_forest


_CONTRIBUTOR_REGISTRIES = weakref.WeakKeyDictionary()
_CONTRIBUTOR_REGISTRIES_BY_ID = {}
_CONTRIBUTOR_LOCK = threading.Lock()


class CoreApiAdapter:
    """Реализация :class:`sdk.kernel.CoreApi` поверх фасада ядра."""

    _sdk_api_kind = "core"

    def __init__(self, provider, contributors=None):
        self._provider = provider
        self._owner = ""
        self._scope = None
        self._section_graph_contributors = (
            contributors
            if contributors is not None
            else self._shared_contributors()
        )

    def bind_owner(self, owner, scope=None):
        """Привязать адаптер к модулю: его имя и набор регистраций."""
        self._owner = str(owner or "")
        self._scope = scope

    def _shared_contributors(self):
        """Один реестр на провайдера, без setattr на самом провайдере."""
        owner = self._provider
        if isinstance(owner, CoreApiAdapter):
            return owner._section_graph_contributors

        registry = getattr(owner, "section_graph_contributors", None)
        if registry is not None:
            return registry

        try:
            with _CONTRIBUTOR_LOCK:
                registry = _CONTRIBUTOR_REGISTRIES.get(owner)
                if registry is None:
                    registry = []
                    _CONTRIBUTOR_REGISTRIES[owner] = registry
                return registry
        except TypeError:
            with _CONTRIBUTOR_LOCK:
                key = id(owner)
                item = _CONTRIBUTOR_REGISTRIES_BY_ID.get(key)
                if item is None or item[0] is not owner:
                    item = (owner, [])
                    _CONTRIBUTOR_REGISTRIES_BY_ID[key] = item
                return item[1]

    @classmethod
    def wrap(cls, provider, contributors=None):
        if getattr(provider, _SDK_API_MARKER, None) == cls._sdk_api_kind:
            return provider
        return cls(provider, contributors=contributors)

    def detect_config_dir(self, candidates):
        return self._provider.resolve.detect_config_dir(candidates)

    def find_system_ltx(self, selected_dir):
        return self._provider.tree.find_system_ltx(selected_dir)

    def resolve_scan_dir(self, config_dir):
        return self._provider.tree.resolve_scan_dir(config_dir)

    def build_tree(self, scan_dir, system_root, log=None, clean=False,
                   overlay=None):
        raw = self._provider.tree.build_tree(
            scan_dir,
            system_root,
            log=log,
            clean=clean,
            overlay=overlay,
        )
        return forest_ref(raw)

    def tree_summary(self, forest):
        return summarize_forest(unwrap_forest(forest))

    def relative_path(self, forest, path):
        return self._provider.tree.relative_path(unwrap_forest(forest), path)

    def forest_scan_dir(self, forest):
        forest = unwrap_forest(forest)
        value = getattr(forest, "scan_dir", None)
        return Path(value) if value else None

    def forest_system_root(self, forest):
        forest = unwrap_forest(forest)
        value = getattr(forest, "system_root", None)
        return Path(value) if value else None

    def game_root(self, forest):
        return self._provider.tree.game_root(unwrap_forest(forest))

    def add_virtual_file(self, forest, path, text):
        return self._provider.tree.add_virtual_file(
            unwrap_forest(forest),
            path,
            text,
        )

    def patch_target_info(self, forest, path):
        target = self._provider.tree.patch_target_info(
            unwrap_forest(forest),
            path,
        )
        return _to_patch_target_info(target)

    def patch_target(self, forest, path):
        return self._provider.tree.patch_target(unwrap_forest(forest), path)

    def read_text(self, path):
        return self._provider.tree.read_text(path)

    def read_xml_text(self, path):
        return self._provider.tree.read_xml_text(path)

    def color_definitions(self, forest, overlay=None, config_dir=None):
        return self._provider.data.color_definitions(
            unwrap_forest(forest),
            overlay=overlay,
            config_dir=config_dir,
        )

    def ltx_section_at_line(self, text, line_number):
        return self._provider.resolve.ltx_section_at_line(text, line_number)

    def ltx_section_bounds(self, text, section):
        return self._provider.resolve.ltx_section_bounds(text, section)

    def ltx_field_line(self, text, section, key):
        return self._provider.resolve.ltx_field_line(text, section, key)

    def ltx_key_from_line(self, line):
        return self._provider.resolve.ltx_key_from_line(line)

    def ltx_declared_sections(self, text):
        return self._provider.resolve.ltx_declared_sections(text)

    def ltx_edit_section(
        self,
        text,
        section,
        key,
        action,
        new_line=None,
        after_key=None,
    ):
        return self._provider.resolve.ltx_edit_section(
            text,
            section,
            key,
            action,
            new_line=new_line,
            after_key=after_key,
        )

    def start_prebuild(self, forest, config_dir, overlay=None):
        return self._provider.prebuild.start(
            unwrap_forest(forest),
            config_dir,
            overlay=overlay,
        )

    def ensure_prebuild(self, forest=None, config_dir=None, overlay=None):
        return self._provider.prebuild.ensure(
            unwrap_forest(forest),
            config_dir,
            overlay=overlay,
        )

    def prebuild_status(self):
        return self._provider.prebuild.status()

    def wait_prebuild(self, log=None, timeout=None):
        return self._provider.prebuild.wait(log=log, timeout=timeout)

    def stop_prebuild(self):
        return self._provider.prebuild.stop()

    def run_resolver(self, forest, config_dir, log=None):
        return self._provider.resolve.run_resolver(
            unwrap_forest(forest),
            config_dir,
            log=log,
        )

    def assemble_text(self, root_path, forest, config_dir, log=None,
                      overlay=None):
        return self._provider.resolve.assemble_text(
            root_path,
            unwrap_forest(forest),
            config_dir,
            log=log,
            overlay=overlay,
        )

    def assemble_to_file(self, root_path, forest):
        return self._provider.resolve.assemble_to_file(
            root_path,
            unwrap_forest(forest),
        )

    def assemble_resolved(self, root_path, forest, config_dir, log=None,
                          overlay=None):
        return self._provider.resolve.assemble_resolved(
            root_path,
            unwrap_forest(forest),
            config_dir,
            log=log,
            overlay=overlay,
        )

    def resolve_xml(self, path, config_dir, log=None, dxml=True,
                    overlay=None):
        raw = self._provider.resolve.resolve_xml(
            path,
            config_dir,
            log=log,
            dxml=dxml,
            overlay=overlay,
        )
        return XmlResolveResult(
            text=raw.text,
            virtual_name=raw.virtual_name,
            dxml_applied=bool(raw.dxml_applied),
            notes=tuple(raw.notes or ()),
            line_sources=tuple(getattr(raw, "line_sources", ()) or ()),
        )

    def resolve_xml_to_file(self, path, config_dir, log=None, dxml=True,
                            overlay=None):
        return self._provider.resolve.resolve_xml_to_file(
            path,
            config_dir,
            log=log,
            dxml=dxml,
            overlay=overlay,
        )

    def build_section_graph(self, forest, section_name, vfs_mode=False,
                            log=None, overlay=None):
        graph = self._provider.graph.build_section_graph(
            unwrap_forest(forest),
            section_name,
            vfs_mode=vfs_mode,
            log=log,
            overlay=overlay,
        )
        return _to_section_graph_view(graph)

    def register_section_graph_contributor(self, contributor):
        if not callable(contributor):
            raise TypeError("contributor должен быть вызываемым")
        entry = (self._owner, contributor)
        if entry not in self._section_graph_contributors:
            self._section_graph_contributors.append(entry)
        handle = RegistrationHandle(
            lambda: self._remove_contributor(contributor, self._owner),
            self._owner,
        )
        if self._scope is not None:
            self._scope.track(handle)
        return handle

    def unregister_section_graph_contributor(self, contributor, owner=None):
        return self._remove_contributor(
            contributor,
            self._owner if owner is None else owner,
        )

    def _remove_contributor(self, contributor, owner=None):
        wanted = None if owner is None else str(owner)
        for entry in tuple(self._section_graph_contributors):
            entry_owner, entry_contributor = entry
            if entry_contributor is not contributor:
                continue
            if wanted is not None and entry_owner != wanted:
                continue
            return remove_identity(
                self._section_graph_contributors,
                entry,
            )
        return False

    def collect_section_graph_contribution(self, request, log=None):
        children = []
        lost_children = []
        edge_styles = {}
        openers = {}
        for _owner, contributor in tuple(self._section_graph_contributors):
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
        return self._provider.resolve.open_external_editor(path)

    def open_with_shell(self, path):
        return self._provider.resolve.open_with_shell(path)
