# -*- coding: utf-8 -*-
"""DocumentApiAdapter: текст проекта и resolved-документы."""

from __future__ import annotations

from sdk.contracts.events import Events
from sdk.features.documents.models import ResolvedLineEdit

from .common import _SDK_API_MARKER


class DocumentApiAdapter:
    """Реализация :class:`sdk.features.documents.DocumentApi`."""

    _sdk_api_kind = "documents"

    def __init__(self, platform, events=None):
        self._platform = platform
        self._events = events
        self._owner = ""
        self._scope = None

    def bind_owner(self, owner, scope=None):
        self._owner = str(owner or "")
        self._scope = scope

    @classmethod
    def wrap(cls, platform, events=None):
        if getattr(platform, _SDK_API_MARKER, None) == cls._sdk_api_kind:
            return platform
        return cls(platform, events=events)

    def _track(self, handle):
        if self._scope is not None:
            self._scope.track(handle)
        return handle

    def open(self, path, line_number=None):
        if line_number:
            self._platform.commands.execute(
                "ui.open_file_at_line",
                path,
                int(line_number),
            )
        else:
            self._platform.commands.execute("ui.open_file", path)

    def read(self, path):
        return self._platform.document_text(path)

    def apply(self, tab_id, edit: ResolvedLineEdit):
        return self._platform.apply_resolved_line_edit(
            tab_id,
            edit.action,
            edit.line_number,
            edit.text,
        )

    def subscribe(self, handler):
        if self._events is None:
            raise RuntimeError("DocumentApi: шина событий недоступна")
        return self._track(
            self._events.subscribe(Events.RESOLVED_DOCUMENT_CHANGED, handler)
        )

    def read_project_text(self, path):
        return self._platform.document_text(path)

    def write_project_text(self, path, text):
        return self._platform.write_project_text(path, text)

    def write_project_text_async(
        self,
        path,
        text,
        on_done=None,
        original_text=None,
    ):
        return bool(
            self._platform.write_project_text_async(
                path,
                text,
                on_done=on_done,
                original_text=original_text,
            )
        )

    def write_project_texts(self, files):
        return self._platform.write_project_texts(files)

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

    def apply_resolved_field_edit_async(
        self,
        tab_id,
        section,
        key,
        value,
        on_done=None,
    ):
        return self._platform.apply_resolved_field_edit_async(
            tab_id,
            section,
            key,
            value,
            on_done=on_done,
        )

    def rename_resolved_field_async(
        self,
        tab_id,
        section,
        old_key,
        new_key,
        value,
        on_done=None,
    ):
        return self._platform.rename_resolved_field_async(
            tab_id,
            section,
            old_key,
            new_key,
            value,
            on_done=on_done,
        )

    def undo_resolved_document(self, tab_id):
        return self._platform.undo_resolved_document(tab_id)

    def redo_resolved_document(self, tab_id):
        return self._platform.redo_resolved_document(tab_id)

    def resolved_section_at_line(self, tab_id, line_number):
        return self._platform.resolved_section_at_line(tab_id, line_number)

    def retain_resolved_document(self, tab_id):
        return self._platform.retain_resolved_document(tab_id)

    def release_resolved_document(self, tab_id):
        return self._platform.release_resolved_document(tab_id)

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

    def open_resolved_tab(self, tab_id, title, text, line_sources, root_path,
                          line_number=None, request_id=None):
        self._platform.open_resolved_tab(
            tab_id,
            title,
            text,
            line_sources,
            root_path,
            line_number,
            request_id=request_id,
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
                              line_sources=(), line_number=None,
                              request_id=None):
        self._platform.open_xml_resolved_tab(
            tab_id,
            title,
            text,
            root_path,
            line_sources,
            line_number,
            request_id=request_id,
        )
