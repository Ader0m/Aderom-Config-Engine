# -*- coding: utf-8 -*-
"""
PlatformApi — сервисы UI-платформы и точка регистраций для SDK.

Именно этот объект модуль видит как ctx.ui.
"""

import os
import re
import time
from dataclasses import replace
from pathlib import Path

from PyQt5.QtWidgets import QApplication, QFileDialog, QMessageBox

from core.tools.fs import read_text_safe, read_xml_text
from sdk.api import ProjectOverlay, ResolvedDraftInfo
from sdk.events import Events

from .file_open_router import FileOpenRouter
from .input_bindings import MouseBindingManager, ShortcutManager
from .module_windows import ModuleWindowManager
from .project import ProjectError, ProjectService
from .project_dialogs import (
    ProjectCreateDialog,
    ProjectLoadDialog,
    ProjectPreviewDialog,
    ProjectSettingsDialog,
)


def _path_key(path):
    """Нормализовать путь для сопоставления запроса открытия и события."""
    return os.path.normcase(os.path.abspath(os.fspath(path)))


class PlatformApi:
    def __init__(self, events, commands, tasks, services, paths):
        self.events = events
        self.commands = commands
        self.tasks = tasks
        self.services = services
        self.paths = paths

        self.router = FileOpenRouter()
        self._window_manager = ModuleWindowManager()
        self._shortcut_manager = ShortcutManager(self.trigger_action)
        self._mouse_manager = MouseBindingManager(self.trigger_action)

        self.window = None
        self._menu_reg = []      # (menu_title, label, callback, shortcut, action)
        self._ctx_actions = []   # (label, handler(path), predicate(path)|None)
        self._log_buffer = []

        projects_dir = getattr(paths, "projects_dir", None)
        if projects_dir is None:
            # Проекты лежат рядом с каталогом src, а не внутри него.
            projects_dir = (
                Path(getattr(paths, "app_dir", ".")).resolve().parent
                / "projects"
            )
        self.project = ProjectService(projects_dir)
        self._forest = None
        self._override_theme_enabled = None
        self._virtual_paths = set()

        # Отложенная навигация: ключ — нормализованный путь файла.
        # Запрос живёт до события FILE_OPENED ровно для этого пути.
        self._pending_file_actions = {}

        self._file_opened_sub = events.subscribe(
            Events.FILE_OPENED,
            self._on_file_opened,
        )
        self._file_open_failed_sub = events.subscribe(
            Events.FILE_OPEN_FAILED,
            self._on_file_open_failed,
        )
        self._tree_ready_sub = events.subscribe(
            Events.TREE_READY,
            self._on_tree_ready,
        )

        commands.register("ui.open_file", self.request_open_file)
        commands.register("ui.open_file_raw", self.request_open_file_raw)
        commands.register("ui.open_file_raw_at_line", self.request_open_file_raw_at_line)
        commands.register("ui.editor_undo", self.request_editor_undo)
        commands.register("ui.editor_redo", self.request_editor_redo)
        commands.register("ui.editor_find", self.request_editor_find)
        commands.register("ui.open_file_at_line", self.request_open_file_at_line)
        commands.register("ui.open_file_with_search", self.request_open_file_with_search)


    # -------------------------------------------------- регистрации (SDK)
    def register_menu(self, menu_title, label, callback, shortcut=None, action=None):
        self._menu_reg.append((menu_title, label, callback, shortcut, action))

    def register_tree_context_action(self, label, handler, predicate=None):
        """Добавить действие в контекстное меню дерева.

        ``predicate(path)`` — необязательный фильтр: действие показывается
        только для путей, для которых он вернул truthy.
        """
        self._ctx_actions.append((label, handler, predicate))

    def register_shortcut(self, key, callback, scope="global", action=None):
        self._shortcut_manager.register(key, callback, scope=scope, action=action)

    def register_mouse_binding(
        self,
        button,
        modifiers,
        callback,
        scope="global",
        event="press",
        action=None,
    ):
        self._mouse_manager.register(
            button,
            modifiers,
            callback,
            scope=scope,
            event=event,
            action=action,
        )

    def register_action_handler(self, action, handler, priority=0):
        return self.events.subscribe(action, handler, priority)

    def dispatch_action(self, action, payload=None):
        return self.events.dispatch(action, payload)

    def trigger_action(self, action, callback, payload=None, fallback=None):
        """Выполнить UI-действие с возможностью перехвата.

        Сначала рассылается конкретное ``action``, затем общее действие
        ``fallback`` (например, ``ui.shortcut``). Если любое из них
        перехвачено, ``callback`` не вызывается.
        """
        if action and self.events.dispatch(action, payload):
            return True
        if fallback and self.events.dispatch(fallback, payload):
            return True
        result = callback()
        return bool(result)

    @property
    def menu_registrations(self):
        return list(self._menu_reg)

    @property
    def context_actions(self):
        return list(self._ctx_actions)

    # -------------------------------------------------- привязка окна
    def bind_window(self, window):
        self.window = window
        self._override_theme_enabled = None
        self._window_manager.bind_main_window(window)
        self._shortcut_manager.attach(window)
        self._mouse_manager.attach(window)

        editor = getattr(window, "editor", None)
        if editor is not None:
            editor.document_changed.connect(self.on_document_changed)
            editor.save_requested.connect(self.save_current_document)
            editor.lexer_status.connect(self.on_lexer_status)
            editor.set_editable(self.can_edit_documents())

        buf, self._log_buffer = self._log_buffer, []
        for m in buf:
            window.log(m)

        self._update_project_status()

    # -------------------------------------------------- проект
    def _on_tree_ready(self, forest):
        self._forest = forest

    def has_forest(self):
        return self._forest is not None

    def can_edit_documents(self):
        return self.project.is_active

    def is_project_active(self):
        return self.project.is_active

    def project_name(self):
        if not self.project.is_active:
            return ""
        return str(self.project.active.name or "")

    def project_overlay(self):
        """Единый слой правок активного проекта для VFS."""
        if not self.project.is_active:
            return ProjectOverlay()

        source_files = {}
        new_files = set()
        for draft in self.project.active.drafts.values():
            path = Path(draft.path)
            source_files[path] = str(draft.current_text or "")
            if draft.is_new:
                new_files.add(path)

        resolved_edits = {}
        for draft in self.project.active.resolved_drafts.values():
            resolved_edits[Path(draft.root_path)] = (
                str(draft.original_text or ""),
                str(draft.current_text or ""),
                tuple(draft.line_sources or ()),
            )

        return ProjectOverlay(
            source_files=source_files,
            resolved_edits=resolved_edits,
            new_files=frozenset(new_files),
        )

    def _restore_project_virtual_files(self):
        """Восстановить новые файлы проекта в текущем дереве VFS."""
        forest = self._forest
        if forest is None:
            return 0, 0

        overlay = self.project_overlay()
        wanted = {
            Path(path)
            for path in overlay.source_files
            if Path(path).suffix.lower() in (".ltx", ".xml")
        }

        removed = 0
        for path in list(self._virtual_paths - wanted):
            try:
                if self.services.remove_virtual_file(forest, path):
                    removed += 1
            except Exception as exc:
                self.log("Проект: не удалось убрать %s из VFS: %s" % (path, exc))
            self._virtual_paths.discard(path)

        added = 0
        for path, text in overlay.source_files.items():
            path = Path(path)
            if path.suffix.lower() not in (".ltx", ".xml"):
                continue
            if path in forest.files or path in forest.xml_files:
                continue
            try:
                if self.services.add_virtual_file(forest, path, text):
                    self._virtual_paths.add(path)
                    added += 1
            except Exception as exc:
                self.log(
                    "Проект: не удалось добавить %s в VFS: %s"
                    % (path, exc)
                )

        if added or removed:
            self.events.publish(Events.TREE_READY, forest)
            try:
                self.services.resolver.invalidate_cache()
            except Exception:
                pass
            self.log(
                "Проект: VFS обновлён — добавлено файлов: %d, удалено: %d"
                % (added, removed)
            )
        return added, removed

    def write_project_text(self, path, text):
        """Сохранить новый текст файла в черновик активного проекта."""
        if not self.project.is_active:
            self.set_status(
                "Создайте проект, чтобы создавать файлы",
                7000,
            )
            return False

        try:
            self.project.save_draft(path, text)
        except ProjectError as exc:
            self.show_error(str(exc))
            return False

        try:
            self.services.resolver.invalidate_cache()
        except Exception as exc:
            self.log("Проект: не удалось сбросить кэш резолва: %s" % exc)

        if self.window is not None:
            editor = getattr(self.window, "editor", None)
            if editor is not None:
                try:
                    editor.mark_clean(path, text)
                except Exception:
                    pass

        self._update_project_status()
        return True

    def resolved_draft(self, tab_id):
        if not self.project.is_active:
            return None
        draft = self.project.get_resolved_draft(tab_id)
        if draft is None:
            return None
        return ResolvedDraftInfo(
            text=draft.current_text,
            saved_text=draft.saved_text,
            original_text=draft.original_text,
            line_sources=tuple(draft.line_sources or ()),
        )

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
        if not self.project.is_active:
            self.set_status(
                "Создайте проект, чтобы изменять файлы",
                7000,
            )
            return False

        try:
            self.project.update_resolved_draft(
                tab_id,
                root_path,
                title,
                original_text,
                current_text,
                line_sources=line_sources,
            )
        except ProjectError as exc:
            self.show_error(str(exc))
            return False

        self._update_project_status()

        if line_edit is not None:
            self.sync_resolved_line_edit(tab_id, line_edit)
        else:
            self.sync_resolved_tab(
                tab_id,
                current_text,
                line_sources,
                root_path,
            )
        return True

    def sync_resolved_line_edit(self, tab_id, edit):
        """Точечно обновить открытую resolved-вкладку."""
        if self.window is None:
            return
        editor = getattr(self.window, "editor", None)
        if editor is None:
            return

        getter = getattr(editor, "get_tab", None)
        view = getter(tab_id) if callable(getter) else None
        if view is None or not hasattr(view, "apply_line_edit"):
            return

        view.apply_line_edit(
            edit.action,
            edit.line_number,
            edit.text,
        )

        draft = (
            self.project.get_resolved_draft(tab_id)
            if self.project.is_active
            else None
        )
        if draft is not None:
            view.set_dirty_state(draft.is_dirty)

    def sync_resolved_tab(self, tab_id, text, line_sources, root_path):
        """Обновить открытую resolved-вкладку без переключения на неё."""
        if self.window is None:
            return
        editor = getattr(self.window, "editor", None)
        if editor is None:
            return

        getter = getattr(editor, "get_tab", None)
        view = getter(tab_id) if callable(getter) else None
        if view is None or not hasattr(view, "set_content"):
            return

        first_line = 0
        cursor = (0, 0)
        widget = getattr(view, "editor", None)
        if widget is not None:
            try:
                first_line = widget.firstVisibleLine()
                cursor = widget.getCursorPosition()
            except Exception:
                pass

        view.set_content(text, line_sources, root_path)

        draft = (
            self.project.get_resolved_draft(tab_id)
            if self.project.is_active
            else None
        )
        if draft is not None:
            view.mark_clean(draft.saved_text)

        if widget is not None:
            try:
                widget.setFirstVisibleLine(first_line)
                widget.setCursorPosition(cursor[0], cursor[1])
            except Exception:
                pass

    def save_project_now(self):
        if not self.project.is_active:
            self.set_status(
                "Создайте проект, чтобы сохранять изменения",
                7000,
            )
            return 0

        try:
            saved = self.project.flush()
        except Exception as exc:
            self.log("Проект: ошибка сохранения: %s" % exc)
            return 0

        if self.window is not None:
            for event in saved:
                if event.kind == "file":
                    self.window.editor.mark_clean(event.path, event.text)
                else:
                    self.window.editor.mark_tab_clean(event.key)

        self.set_status(
            "Проект сохранён: %d файл(ов)" % len(saved),
            4000,
        )
        self._update_project_status()
        return len(saved)

    def document_text(self, path):
        """Текст файла для редактора: черновик проекта или оригинал."""
        path = Path(path)
        if self.project.is_active:
            text = self.project.get_text(path)
            if text is not None:
                return text
        try:
            if path.suffix.lower() == ".xml":
                return read_xml_text(path)
            return read_text_safe(path)
        except OSError:
            return ""

    def on_document_changed(self, path, text):
        if not self.project.is_active:
            return
        try:
            self.project.update_draft(path, text)
        except ProjectError as exc:
            self.log("Проект: не удалось обновить черновик: %s" % exc)
            return

        # Черновик входит в VFS: ранее собранные корни больше нельзя
        # считать актуальными, даже если файл на диске не менялся.
        try:
            self.services.resolver.invalidate_cache()
        except Exception as exc:
            self.log("Проект: не удалось сбросить кэш резолва: %s" % exc)

        self._update_project_status()

    def on_resolved_changed(self, tab_id, view, text):
        """Пользователь правит итоговый (resolved) текст."""
        if not self.project.is_active:
            return

        title = "%s (resolved)" % Path(view.root_path).name

        try:
            self.project.update_resolved_draft(
                tab_id,
                view.root_path,
                title,
                view.baseline_text,
                text,
                line_sources=view.line_sources,
            )
        except ProjectError as exc:
            self.log("Проект: не удалось обновить resolved-черновик: %s" % exc)
            return

        try:
            self.services.resolver.invalidate_cache()
        except Exception as exc:
            self.log("Проект: не удалось сбросить кэш резолва: %s" % exc)

        self._update_project_status()

    def save_resolved_document(self, tab_id, view):
        if not self.project.is_active:
            self.set_status(
                "Создайте проект, чтобы сохранять изменения",
                7000,
            )
            return

        try:
            self.project.save_resolved_draft(tab_id, view.text())
        except ProjectError as exc:
            self.show_error(str(exc))
            return

        if self.window is not None:
            self.window.editor.mark_tab_clean(tab_id)

        self.set_status("Resolved сохранён в проекте", 5000)
        self.log("Проект: сохранён resolved-черновик %s" % tab_id)
        self._update_project_status()

    def save_current_document(self, path=None, text=None):
        if not self.project.is_active:
            self.set_status(
                "Создайте проект, чтобы сохранять изменения файлов",
                7000,
            )
            return

        if path is None or text is None:
            editor = None
            if self.window is not None:
                editor = self.window.editor.current_editor()
            if editor is None or not editor.current_file:
                self.set_status("Нет активного текстового файла", 5000)
                return
            path = editor.current_file
            text = editor.text()

        try:
            self.project.save_draft(path, text)
        except ProjectError as exc:
            self.show_error(str(exc))
            return

        if self.window is not None:
            self.window.editor.mark_clean(path, text)

        self.set_status(
            "Сохранено в проекте: %s" % Path(path).name,
            5000,
        )
        self.log("Проект: сохранён черновик %s" % Path(path).name)
        self._update_project_status()

    def create_project(self):
        if self.window is None:
            return

        dialog = ProjectCreateDialog(self.window)
        if not dialog.exec_():
            return

        name, modules, save_mode, xml_patch_mode = dialog.values()

        try:
            project = self.project.create(
                name,
                modules,
                save_mode,
                xml_patch_mode=xml_patch_mode,
            )
        except ProjectError as exc:
            self.show_error(str(exc))
            return

        self._restore_project_virtual_files()
        self.window.editor.set_editable(True)
        self._reload_open_documents()
        self._update_project_status()
        self.log("Проект создан: %s" % project.name)
        self.set_status("Проект создан: %s" % project.name, 5000)

    def load_project(self):
        if self.window is None:
            return

        projects = self.project.list_projects()
        if not projects:
            QMessageBox.information(
                self.window,
                "Проект",
                "Сохранённых проектов пока нет.",
            )
            return

        dialog = ProjectLoadDialog(projects, self.window)
        if not dialog.exec_():
            return

        project_id = dialog.selected_id()
        if not project_id:
            return

        try:
            project = self.project.load(project_id)
        except ProjectError as exc:
            self.show_error(str(exc))
            return

        self._restore_project_virtual_files()
        self.window.editor.set_editable(True)
        self._reload_open_documents()
        self._update_project_status()
        self.log("Проект загружен: %s" % project.name)
        self.set_status("Проект загружен: %s" % project.name, 5000)

    def _delete_project(self):
        try:
            self.project.delete_active_project()
        except ProjectError as exc:
            self.show_error(str(exc))
            return False

        self._restore_project_virtual_files()

        if self.window is not None:
            self.window.editor.set_editable(False)

        self._update_project_status()
        self.log("Проект удалён")
        self.set_status("Проект удалён", 5000)
        return True

    def project_settings(self):
        if self.window is None:
            return

        if not self.project.is_active:
            QMessageBox.information(
                self.window,
                "Проект",
                "Сначала создайте или загрузите проект.",
            )
            return

        dialog = ProjectSettingsDialog(
            self.project.active,
            self.window,
            on_delete=self._delete_project,
        )
        if not dialog.exec_():
            return

        if dialog.deleted:
            return

        try:
            project = self.project.set_patch_mode(dialog.save_mode())
            project = self.project.set_xml_patch_mode(
                dialog.xml_patch_mode()
            )
        except ProjectError as exc:
            self.show_error(str(exc))
            return

        self._update_project_status()
        self.log(
            "Проект: DLTX=%s; DXML=%s"
            % (project.save_mode, project.xml_patch_mode)
        )
        self.set_status(
            "Проект: DLTX=%s; DXML=%s"
            % (project.save_mode, project.xml_patch_mode),
            5000,
        )

    def _reload_open_documents(self):
        if self.window is None:
            return
        editor = getattr(self.window, "editor", None)
        if editor is None:
            return

        for path in editor.open_paths():
            text = self.document_text(path)
            editor.reload_path(path, text)
            editor.mark_clean(path, text)

        for tab_id, view in editor.resolved_tabs():
            draft = self.project.get_resolved_draft(tab_id)
            if draft is None:
                continue
            view.set_content(
                draft.current_text,
                view.line_sources,
                view.root_path,
            )
            view.mark_clean(draft.saved_text)
            editor.set_tab_dirty(tab_id, draft.is_dirty)

    def autosave_project(self):
        if not self.project.is_active:
            return

        try:
            saved = self.project.autosave()
        except Exception as exc:
            self.log("Проект: ошибка автосохранения: %s" % exc)
            return

        if not saved:
            return

        if self.window is not None:
            for event in saved:
                if event.kind == "file":
                    self.window.editor.mark_clean(event.path, event.text)
                else:
                    self.window.editor.mark_tab_clean(event.key)

        self.set_status(
            "Автосохранение проекта: %d файл(ов)" % len(saved),
            4000,
        )
        self.log("Проект: автосохранено файлов %d" % len(saved))
        self._update_project_status()

    def on_app_close(self):
        if not self.project.is_active:
            return
        try:
            self.project.flush()
        except Exception as exc:
            self.log("Проект: ошибка сохранения при закрытии: %s" % exc)

    def build_patch_preview(self):
        if self.window is None:
            return

        if not self.project.is_active:
            self.show_error("Создайте или загрузите проект.")
            return

        if self._forest is None:
            self.show_error(
                "Дерево не построено: сначала выполните «Чистый запуск»."
            )
            return

        if not self.project.changed_drafts() and not (
            self.project.changed_resolved_drafts()
        ):
            QMessageBox.information(
                self.window,
                "Проект",
                "В проекте нет изменённых файлов.",
            )
            return

        self.autosave_project()
        self.set_status("Подготовка патча проекта…")
        self.log("Проект: подготовка патча…")

        forest = self._forest

        def work(log):
            return self.project.build_preview(
                self.services,
                forest,
                log=log,
            )

        self.tasks.run(
            work,
            on_done=self._on_preview_ready,
            on_log=self.log,
        )

    def _on_preview_ready(self, preview, err):
        if err is not None:
            self.set_status("Ошибка подготовки патча")
            self.log("Проект: ошибка подготовки патча: %s" % err)
            self.show_error(str(err))
            return

        if preview is None:
            return

        self.set_status(
            "Патч подготовлен: %d файл(ов)" % len(preview.items),
            5000,
        )

        dialog = ProjectPreviewDialog(
            preview,
            self.window,
            on_write=self.write_preview,
        )
        dialog.exec_()

    def write_preview(self, preview):
        if self._forest is None:
            self.show_error("Нет построенного дерева.")
            return False

        directory = QFileDialog.getExistingDirectory(
            self.window,
            "Выберите каталог, в котором будет создан патч-мод",
            "",
            QFileDialog.ShowDirsOnly,
        )
        if not directory:
            return False

        safe_name = re.sub(
            r'[<>:"/\\|?*]+',
            "_",
            preview.project.name,
        ).strip(" .") or "ACE Patch"

        mod_dir = Path(directory) / ("ACE Patch - " + safe_name)
        written = []
        skipped = []

        for item in preview.items:
            if not item.is_patch:
                continue
            if not item.ok:
                skipped.append(item.title)
                continue

            plan = item.plan
            if plan.needs_confirmation:
                plan = replace(plan, confirmed=True)
            generated = replace(item.generated, plan=plan)

            try:
                paths = self.services.write_patch(
                    generated,
                    mod_dir,
                    forbidden_roots=[Path(self._forest.scan_dir)],
                    overwrite=False,
                )
                written.extend(paths)
            except Exception as exc:
                self.show_error(
                    "Не удалось записать %s:\n%s"
                    % (item.title, exc)
                )
                return False

        if not written:
            self.show_error("Патчи не записаны.")
            return False

        self.set_status(
            "Патч-мод создан: %s" % mod_dir.name,
            7000,
        )
        self.log("Проект: записано файлов патча %d" % len(written))

        if skipped:
            QMessageBox.warning(
                self.window,
                "Проект",
                "Записаны не все файлы. Пропущены:\n%s"
                % "\n".join(skipped),
            )
        else:
            QMessageBox.information(
                self.window,
                "Проект",
                "Патч-мод создан:\n%s" % mod_dir,
            )

        return True

    def _update_project_status(self):
        if self.window is None:
            return
        setter = getattr(self.window, "set_project_status", None)
        if callable(setter):
            setter(self.project.status_text())

        # Держим доступность «Сформировать патч» в актуальном состоянии,
        # не дожидаясь повторного открытия меню.
        updater = getattr(self.window, "_update_project_menu", None)
        if callable(updater):
            updater()

        self._apply_override_warning()

    def _apply_override_warning(self):
        """Красные полосы и заголовок при опасном режиме Override."""
        if self.window is None:
            return

        mode = self.project.patch_mode() if self.project.is_active else ""
        xml_mode = (
            self.project.active.xml_patch_mode
            if self.project.is_active
            else ""
        )
        enabled = mode == "override" or xml_mode == "xml_override"

        if enabled == self._override_theme_enabled:
            return
        self._override_theme_enabled = enabled

        try:
            from .theme import apply_override_theme, apply_window_caption

            apply_override_theme(QApplication.instance(), enabled)
            apply_window_caption(self.window, enabled)
        except Exception as exc:
            self.log("Theme: не удалось применить Override-оформление: %s" % exc)

        self.window.setWindowTitle(
            "Внимание - включен опасный режим работы"
            if enabled
            else "LTX Explorer"
        )

    # -------------------------------------------------- ui-сервисы
    def on_lexer_status(self, message):
        """Обновляемая строка покраски файла в диагностике."""
        self.log_status("ui.lexer", message)
        if message.endswith("готово") or message.endswith("…"):
            self.set_status(message, 5000)

    def log(self, msg):
        if self.window is not None:
            self.window.log(msg)
        else:
            self._log_buffer.append(str(msg))

    def log_status(self, key, msg):
        """Обновить статусную строку диагностики (прогресс, кэш и т.п.)."""
        if self.window is not None:
            self.window.log_status(str(key), str(msg))
        else:
            self._log_buffer.append(str(msg))

    def set_status(self, msg, timeout=0):
        if self.window is not None:
            self.window.statusBar().showMessage(str(msg), timeout)

    def show_error(self, text):
        if self.window is not None:
            QMessageBox.critical(self.window, "Ошибка", str(text))
        else:
            print(text)

    def ask_directory(self, initial=""):
        d = QFileDialog.getExistingDirectory(
            self.window,
            "Выбрать папку с конфигами",
            initial or "",
            QFileDialog.ShowDirsOnly,
        )
        return d or None

    def clipboard_set(self, text):
        QApplication.clipboard().setText(str(text))

    def theme_color(self, name, fallback=None):
        from .theme import PALETTE

        return getattr(PALETTE, str(name), fallback)

    # -------------------------------------------------- команды платформы
    def request_open_file(self, path):
        ok = self.router.open(path, self)
        if not ok:
            self.show_error("Не найден обработчик для открытия файла: %s" % path)
        return ok

    # -------------------------------------------------- регистрации (SDK)
    def register_open_handler(self, handler):
        """Модуль регистрирует своего обработчика открытия файла."""
        self.router.register(handler)

    # -------------------------------------------------- окна модулей
    def show_module_window(self, key, window):
        """Показать окно, созданное модулем."""
        self._window_manager.show(key, window)
        self.events.publish(Events.UI_WINDOW_SHOWN, key)

    def close_module_window(self, key):
        """Закрыть окно модуля по ключу."""
        self._window_manager.close(key)
        self.events.publish(Events.UI_WINDOW_CLOSED, key)

    # -------------------------------------------------- вкладки редактора
    def open_editor_tab(self, tab_id, title, widget):
        """Открыть вкладку, созданную модулем."""
        if self.window is None:
            self.log("[open_editor_tab до окна] %s" % tab_id)
            return
        self.window.editor.open_tab(tab_id, title, widget)

    def close_editor_tab(self, tab_id):
        """Закрыть вкладку модуля."""
        if self.window is None:
            return
        self.window.editor.close_tab(tab_id)

    # -------------------------------------------------- ui-сервисы
    def show_text(self, text, label, readonly=True, source_path=None):
        if self.window is not None:
            self.window.editor.show_text(
                text,
                label,
                readonly=readonly,
                source_path=source_path,
            )
        else:
            self._log_buffer.append("[show_text до окна] %s" % label)

    def request_open_file_raw(self, path):
        ok = self.router.open_default(path, self)
        if not ok:
            self.show_error("Нет обработчика по умолчанию для: %s" % path)
        return ok

    def request_open_file_raw_at_line(self, path, line_number: int):
        """Открыть исходный файл (без резолва) и перейти к строке."""
        key = _path_key(path)
        self._pending_file_actions[key] = ("line", int(line_number))

        ok = self.router.open_default(path, self)
        if not ok:
            self._pending_file_actions.pop(key, None)
            self.show_error("Нет обработчика по умолчанию для: %s" % path)
        return ok

    def request_editor_undo(self):
        if self.window is not None:
            self.window.editor.undo()

    def request_editor_redo(self):
        if self.window is not None:
            self.window.editor.redo()

    def request_editor_find(self):
        if self.window is not None:
            self.window.editor.show_find()

    def ask_text(self, prompt: str, initial: str = "") -> str:
        """Запросить текст у пользователя через диалог."""
        from PyQt5.QtWidgets import QInputDialog
        
        text, ok = QInputDialog.getText(
            self.window,
            "Ввод",
            prompt,
            text=initial,
        )
        return text if ok and text else ""

    def request_open_file_at_line(self, path, line_number: int):
        """Открыть файл и перейти к указанной строке.

        Навигация применяется не сразу: файл может открываться асинхронно
        (резолв .ltx). Запрос ждёт события :attr:`Events.FILE_OPENED` для
        этого же пути.
        """
        key = _path_key(path)
        self._pending_file_actions[key] = ("line", int(line_number))

        ok = self.router.open(path, self)
        if not ok:
            self._pending_file_actions.pop(key, None)
            self.show_error("Не найден обработчик для открытия файла: %s" % path)
        return ok

    def request_open_file_with_search(self, path, search_text):
        """Открыть файл и перейти к первому вхождению ``search_text``.

        Навигация откладывается до факта открытия именно этого пути:
        для резолва .ltx виджет создаётся позже, после фоновой сборки.
        """
        key = _path_key(path)
        self._pending_file_actions[key] = ("search", search_text)

        ok = self.router.open(path, self)
        if not ok:
            self._pending_file_actions.pop(key, None)
            self.show_error("Не найден обработчик для: %s" % path)
        return ok

    # -------------------------------------------------- факты открытия
    def _on_file_opened(self, path):
        """Событие FILE_OPENED: применить навигацию ровно для этого пути."""
        pending = self._pending_file_actions.pop(_path_key(path), None)
        if pending is None or self.window is None:
            return

        kind, value = pending
        widget = self.window.editor.currentWidget()

        if kind == "search":
            if widget is not None and hasattr(widget, "find_first"):
                if value and not widget.find_first(value):
                    self.set_status(
                        "Владелец открыт, но строка не найдена: %s" % value,
                        8000,
                    )
            else:
                self.log(
                    "[open_file_with_search] активная вкладка не поддерживает "
                    "поиск: %s" % type(widget).__name__
                )
        elif kind == "line":
            if widget is not None and hasattr(widget, "goto_line"):
                widget.goto_line(value)
            else:
                self.log(
                    "[open_file_at_line] активная вкладка не поддерживает "
                    "переход к строке: %s" % type(widget).__name__
                )

    def _on_file_open_failed(self, path):
        """Файл не открылся: отложенная навигация больше не нужна."""
        if self._pending_file_actions.pop(_path_key(path), None) is not None:
            self.log("[open] навигация отменена, файл не открыт: %s" % path)

    def _log_slow_view(self, kind, title, content_elapsed, tab_elapsed):
        """Диагностика: показать, сколько заняла отрисовка вкладки."""
        total = content_elapsed + tab_elapsed
        if total >= 0.5:
            self.log(
                "[%s] отрисовка %.2f s (контент %.2f, вкладка %.2f): %s"
                % (kind, total, content_elapsed, tab_elapsed, title)
            )

    def open_resolved_tab(self, tab_id, title, text, line_sources, root_path,
                          line_number=None):
        """Создать ResolvedView и открыть его как вкладку."""
        if self.window is None:
            return
        from .editor import ResolvedView  # локальный импорт, чтобы не цеплять PyQt до окна

        started = time.perf_counter()
        view = ResolvedView()
        view.set_editable(self.can_edit_documents())

        existing = (
            self.project.get_resolved_draft(tab_id)
            if self.project.is_active
            else None
        )
        display_text = existing.current_text if existing is not None else text

        view.set_content(display_text, line_sources, root_path)
        if existing is not None:
            view.mark_clean(existing.saved_text)
        content_elapsed = time.perf_counter() - started

        view.source_activated.connect(
            lambda path, search: self.request_open_file_with_search(path, search)
        )
        view.diagnostic.connect(lambda msg: self.log("[resolved] %s" % msg))
        view.diagnostic.connect(lambda msg: self.set_status(msg, 8000))
        view.lexer_status.connect(self.on_lexer_status)
        view.content_changed.connect(
            lambda new_text, tid=tab_id, v=view: self.on_resolved_changed(
                tid,
                v,
                new_text,
            )
        )
        view.save_requested.connect(
            lambda tid=tab_id, v=view: self.save_resolved_document(tid, v)
        )
        view.dirty_changed.connect(
            lambda dirty, tid=tab_id: self.window.editor.set_tab_dirty(
                tid,
                dirty,
            )
        )

        started = time.perf_counter()
        self.window.editor.open_tab(
            tab_id,
            title,
            view,
            source_path=root_path,
        )
        tab_elapsed = time.perf_counter() - started
        self._log_slow_view("resolved", title, content_elapsed, tab_elapsed)

        if existing is not None and existing.is_dirty:
            self.window.editor.set_tab_dirty(tab_id, True)

        if line_number is not None:
            view.goto_line(int(line_number))

    def open_xml_resolved_tab(self, tab_id, title, text, root_path,
                              line_sources=None, line_number=None):
        """Открыть XML-резолв в отдельной вкладке с панелью источников."""
        if self.window is None:
            return
        from .editor import ResolvedView

        sources = list(line_sources or [])
        has_sources = any(src is not None for src in sources)
        started = time.perf_counter()
        view = ResolvedView()
        view.set_editable(self.can_edit_documents())

        existing = (
            self.project.get_resolved_draft(tab_id)
            if self.project.is_active
            else None
        )
        display_text = existing.current_text if existing is not None else text

        view.set_content(
            display_text,
            sources,
            str(root_path),
            sources_visible=has_sources,
        )
        if existing is not None:
            view.mark_clean(existing.saved_text)
        content_elapsed = time.perf_counter() - started

        view.source_activated.connect(
            lambda path, search: self.request_open_file_with_search(path, search)
        )
        view.source_raw_activated.connect(
            lambda path, search: self.request_open_file_raw_with_search(path, search)
        )
        view.diagnostic.connect(lambda msg: self.log("[xml-resolved] %s" % msg))
        view.diagnostic.connect(lambda msg: self.set_status(msg, 8000))
        view.lexer_status.connect(self.on_lexer_status)
        view.content_changed.connect(
            lambda new_text, tid=tab_id, v=view: self.on_resolved_changed(
                tid,
                v,
                new_text,
            )
        )
        view.save_requested.connect(
            lambda tid=tab_id, v=view: self.save_resolved_document(tid, v)
        )
        view.dirty_changed.connect(
            lambda dirty, tid=tab_id: self.window.editor.set_tab_dirty(
                tid,
                dirty,
            )
        )

        started = time.perf_counter()
        self.window.editor.open_tab(
            tab_id,
            title,
            view,
            source_path=str(root_path),
        )
        tab_elapsed = time.perf_counter() - started
        self._log_slow_view("xml-resolved", title, content_elapsed, tab_elapsed)

        if existing is not None and existing.is_dirty:
            self.window.editor.set_tab_dirty(tab_id, True)

        if line_number is not None:
            view.goto_line(int(line_number))

    def request_open_file_raw_with_search(self, path, search_text):
        """Открыть файл как сырой текст и перейти к строке поиска."""
        key = _path_key(path)
        self._pending_file_actions[key] = ("search", search_text)

        ok = self.router.open_default(path, self)
        if not ok:
            self._pending_file_actions.pop(key, None)
            self.show_error("Нет обработчика по умолчанию для: %s" % path)
        return ok
