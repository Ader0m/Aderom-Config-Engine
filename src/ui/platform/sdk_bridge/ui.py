# -*- coding: utf-8 -*-
"""UiApiAdapter: общая UI-платформа без доменных методов."""

from __future__ import annotations

from sdk.contracts.forest import forest_ref

from .common import _SDK_API_MARKER


class UiApiAdapter:
    """Реализация :class:`sdk.ui.UiApi` поверх платформы UI."""

    _sdk_api_kind = "ui"

    def __init__(self, platform):
        self._platform = platform
        self._context = None
        self._owner = ""
        self._scope = None

    def bind_context(self, context):
        """Связать адаптер с ModuleContext конкретного модуля."""
        self._context = context
        self._scope = getattr(context, "registrations", None)
        self._owner = getattr(self._scope, "owner", "") or ""

    def bind_owner(self, owner, scope=None):
        self._owner = str(owner or "")
        self._scope = scope

    @classmethod
    def wrap(cls, platform):
        if getattr(platform, _SDK_API_MARKER, None) == cls._sdk_api_kind:
            return platform
        return cls(platform)

    def _track(self, handle):
        if self._scope is not None:
            self._scope.track(handle)
        return handle

    def register_menu(self, menu_title, label, callback, shortcut=None, action=None):
        handle = self._platform.register_menu(
            menu_title,
            label,
            callback,
            shortcut,
            action,
            owner=self._owner,
        )
        return self._track(handle)

    def register_tree_context_action(self, label, handler, predicate=None):
        handle = self._platform.register_tree_context_action(
            label,
            handler,
            predicate,
            owner=self._owner,
        )
        return self._track(handle)

    def register_file_action(self, label, handler, predicate=None):
        handle = self._platform.register_file_action(
            label,
            handler,
            predicate,
            owner=self._owner,
        )
        return self._track(handle)

    def register_open_handler(self, handler):
        handle = self._platform.register_open_handler(
            handler,
            context=self._context,
            owner=self._owner,
        )
        return self._track(handle)

    def register_shortcut(self, key, callback, scope="global", action=None):
        handle = self._platform.register_shortcut(
            key,
            callback,
            scope=scope,
            action=action,
            owner=self._owner,
        )
        return self._track(handle)

    def register_mouse_binding(
        self,
        button,
        modifiers,
        callback,
        scope="global",
        event="press",
        action=None,
    ):
        handle = self._platform.register_mouse_binding(
            button,
            modifiers,
            callback,
            scope=scope,
            event=event,
            action=action,
            owner=self._owner,
        )
        return self._track(handle)

    def register_action_handler(self, action, handler, priority=0):
        subscription = self._platform.register_action_handler(
            action,
            handler,
            priority,
            owner=self._owner,
        )
        return self._track(subscription)

    def dispatch_action(self, action, payload=None):
        return self._platform.dispatch_action(action, payload)

    def is_project_active(self):
        return self._platform.is_project_active()

    def project_name(self):
        return self._platform.project_name()

    def project_overlay(self):
        return self._platform.project_overlay()

    def refresh_workspace(self):
        return self._platform.refresh_workspace()

    def workspace_forest(self):
        return forest_ref(self._platform.workspace_forest())

    def publish_tree_ready(self, forest):
        return self._platform.publish_tree_ready(forest)

    def workspace_generation(self):
        return self._platform.workspace_generation()

    def set_editor_tab_dirty(self, tab_id, dirty):
        return self._platform.set_editor_tab_dirty(tab_id, dirty)

    def set_editor_tab_busy(self, tab_id, busy):
        return self._platform.set_editor_tab_busy(tab_id, busy)

    def save_project_now(self):
        return self._platform.save_project_now()

    def open_file_with_search(self, path, search_text):
        self._platform.commands.execute("ui.open_file_with_search", path, search_text)

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
                  line_number=None, syntax=True, notice=None,
                  request_id=None, tab_id=None):
        self._platform.show_text(
            text,
            label,
            readonly=readonly,
            source_path=source_path,
            line_number=line_number,
            syntax=syntax,
            notice=notice,
            request_id=request_id,
            tab_id=tab_id,
        )

    def show_module_window(self, key, window):
        return self._platform.show_module_window(
            key,
            window,
            owner=self._owner,
        )

    def close_module_window(self, key):
        self._platform.close_module_window(key)

    def open_editor_tab(self, tab_id, title, widget):
        self._platform.open_editor_tab(tab_id, title, widget)

    def close_editor_tab(self, tab_id):
        self._platform.close_editor_tab(tab_id)

    def release_owner(self):
        """Закрыть окна и вкладки, принадлежащие модулю."""
        self._platform.close_module_windows(owner=self._owner)
        return int(self._platform.close_module_tabs(self._owner) or 0)

    def copy_to_clipboard(self, text):
        self._platform.clipboard_set(text)

    def attach_text_hints(self, widget, target=None, providers=None,
                          enabled=True):
        return self._platform.attach_text_hints(
            widget,
            target=target,
            providers=providers,
            enabled=enabled,
        )

    def theme_color(self, name, fallback=None):
        return self._platform.theme_color(name, fallback)

    def request_window(self, window_id):
        return self._platform.request_window(window_id)

    def request_tab(self, tab_id):
        return self._platform.request_tab(tab_id)

    def request_file_open(self, path):
        return self._platform.request_file_open(path)

    def open_file(self, path):
        self._platform.commands.execute("ui.open_file", path)

    def open_raw(self, path):
        self._platform.commands.execute("ui.open_file_raw", path)

    def notify_file_open_failed(self, path, reason="", request_id=None):
        return self._platform.notify_file_open_failed(
            path,
            reason=reason,
            request_id=request_id,
        )

    def current_file_request_id(self):
        return int(self._platform.current_file_request_id() or 0)

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
        return self._platform.sort_file_tree_by_files_desc()
