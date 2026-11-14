# -*- coding: utf-8 -*-
"""Живые resolved-документы и их вкладки. Часть PlatformApi."""

from __future__ import annotations

import inspect
import time
from pathlib import Path

from app.documents import ResolvedDocument
from app.projects import ProjectError
from sdk.api import ItemCardRequest, ResolvedDraftInfo
from sdk.events import Events


def _call_with_overlay(func, *args, overlay=None, **kwargs):
    """Передать ``overlay`` только тем поставщикам, которые его знают.

    Тестовые и сторонние реализации сервиса могут сохранять старую
    подпись; платформа не должна падать из-за нового необязательного
    аргумента.
    """
    if overlay is None:
        return func(*args, **kwargs)

    try:
        parameters = inspect.signature(func).parameters
    except (TypeError, ValueError):
        parameters = {}

    accepts = "overlay" in parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    if accepts:
        kwargs["overlay"] = overlay
    return func(*args, **kwargs)


class ResolvedDocumentMixin:
    """Операции над живыми resolved-документами."""

    def _on_project_changed(self, payload):
        # Любая смена проекта (создание, загрузка, удаление) делает
        # старые живые документы недействительными. Иначе вкладка могла
        # продолжать писать в проект, который уже закрыт.
        for document in list(self._resolved_documents.values()):
            if self.window is not None:
                view = self.window.editor.get_tab(document.tab_id)
                unbind = getattr(view, "unbind_document", None)
                if callable(unbind):
                    unbind(document)
            document.unbind_view()
        self._resolved_documents.clear()
        self._pending_file_actions.clear()

    def _on_tab_closed(self, tab_id):
        document = self._resolved_documents.get(str(tab_id))
        if document is None:
            return
        document.unbind_view()
        if document.can_drop():
            self._resolved_documents.pop(str(tab_id), None)

    def _refresh_orphan_resolved_tab(self, tab_id, view):
        """Перерезолвить открытую resolved-вкладку из активного проекта.

        Если файл вкладки входит в лес нового проекта, он собирается
        заново с проектным overlay. Возвращает ``True``, если обновление
        поставлено в очередь или выполнено.
        """
        if not self.project.is_active or self.window is None:
            return False

        root_path = getattr(view, "root_path", None)
        if not root_path:
            return False
        path = Path(root_path)

        forest = self.workspace.forest
        if forest is None:
            return False

        roots = self.workspace.roots_for([path])
        if not roots:
            return False

        from sdk.paths import path_key

        if path_key(path) not in {path_key(root) for root in roots}:
            return False

        services = self.services
        is_xml = path.suffix.lower() == ".xml"
        assemble = getattr(services, "assemble_resolved", None)
        resolve_xml = getattr(services, "resolve_xml", None)
        if is_xml and not callable(resolve_xml):
            return False
        if not is_xml and not callable(assemble):
            return False

        config_dir = self.workspace.config_dir or Path(
            getattr(forest, "scan_dir", "") or ""
        )
        overlay = self.project_overlay()
        project_id = str(getattr(self.project.active, "id", ""))
        title = "[Res]%s" % path.name
        document_format = "xml" if is_xml else "ltx"

        def work(log):
            if is_xml:
                result = resolve_xml(path, config_dir, log=log)
                return (
                    result.text,
                    tuple(result.line_sources or ()),
                )
            return assemble(
                path,
                forest,
                config_dir,
                log=log,
                overlay=overlay,
            )

        def done(result, err):
            if err is not None:
                self.log(
                    "Проект: не удалось обновить вкладку %s: %s"
                    % (title, err)
                )
                return
            if result is None:
                return
            if not self.project.is_active:
                return
            if str(getattr(self.project.active, "id", "")) != project_id:
                return

            editor = getattr(self.window, "editor", None)
            if editor is None:
                return
            current = editor.get_tab(tab_id)
            if current is None:
                return

            text, sources = result
            document = self._resolved_documents.get(tab_id)
            if document is not None:
                document.reset(
                    text,
                    line_sources=sources,
                    saved_text=text,
                    original_text=text,
                    persist=False,
                )
            else:
                document = self._ensure_resolved_document(
                    tab_id,
                    root_path=path,
                    title=title,
                    text=text,
                    line_sources=sources,
                    document_format=document_format,
                )
            if document is None:
                return

            if getattr(current, "document", None) is not document:
                current.bind_document(document)
            supported = getattr(current, "set_editing_supported", None)
            if callable(supported):
                supported(True)
            setter = getattr(current, "set_editable", None)
            if callable(setter):
                setter(self.can_edit_documents())
            self._reload_resolved_view(
                current,
                document.current_text,
                document.line_sources,
                document.root_path,
            )
            current.set_dirty_state(document.is_dirty)
            editor.set_tab_dirty(tab_id, document.is_dirty)
            set_tab_busy = getattr(editor, "set_tab_busy", None)
            if callable(set_tab_busy):
                set_tab_busy(tab_id, False)
            self.log("Проект: вкладка обновлена из проекта: %s" % title)

        run = getattr(self.tasks, "run", None)
        if not callable(run):
            try:
                done(work(self.log), None)
            except Exception as exc:
                done(None, exc)
            return True

        run(work, on_done=done, on_log=self.log)
        return True

    def _ensure_resolved_document(
        self,
        tab_id,
        root_path=None,
        title="",
        text="",
        line_sources=(),
        retain=False,
        document_format="ltx",
    ):
        """Вернуть живой документ, создав его из черновика или текста."""
        tab_id = str(tab_id or "")
        if not tab_id:
            return None

        document = self._resolved_documents.get(tab_id)
        if document is None:
            draft = (
                self.project.get_resolved_draft(tab_id)
                if self.project.is_active
                else None
            )
            if draft is not None:
                root_path = draft.root_path
                title = draft.title or title
                text = draft.current_text
                saved_text = draft.saved_text
                original_text = draft.original_text
                line_sources = draft.line_sources or line_sources
            else:
                saved_text = None
                original_text = None

            if root_path is None:
                return None

            document = ResolvedDocument(
                tab_id,
                root_path,
                title or ("[Res]%s" % Path(root_path).name),
                text,
                line_sources=line_sources,
                saved_text=saved_text,
                original_text=original_text,
                events=self.events,
                persist=self._persist_resolved_document,
                save=self._save_resolved_document_state,
                log_fn=self.log,
                document_format=document_format,
            )
            self._resolved_documents[tab_id] = document
            # Вкладка снова привязана к документу — она готова к правке.
            if self.window is not None:
                editor = getattr(self.window, "editor", None)
                set_tab_busy = getattr(editor, "set_tab_busy", None)
                if callable(set_tab_busy):
                    set_tab_busy(tab_id, False)

        if retain:
            document.retain()
        return document

    def ensure_resolved_document(
        self,
        tab_id,
        root_path,
        title="",
        text="",
        line_sources=(),
        document_format="ltx",
    ):
        """Публичная точка создания/получения документа для модулей."""
        document = self._ensure_resolved_document(
            tab_id,
            root_path=root_path,
            title=title,
            text=text,
            line_sources=line_sources,
            document_format=document_format,
        )
        if (
            document is not None
            and text
            and not document.is_dirty
            and not document.has_changes
            and str(text) != document.current_text
        ):
            # Свежий резолв (например, после создания нового файла)
            # обновляет только чистый документ без сохранённых правок.
            if self.project.is_active:
                try:
                    self.project.remove_resolved_draft(document.tab_id)
                except Exception:
                    pass
            document.reset(
                text,
                line_sources=line_sources,
                saved_text=text,
                original_text=text,
                persist=False,
            )
            view = (
                self.window.editor.get_tab(document.tab_id)
                if self.window is not None
                else None
            )
            if view is not None and hasattr(view, "set_content"):
                self._reload_resolved_view(
                    view,
                    document.current_text,
                    document.line_sources,
                    document.root_path,
                )
                view.set_dirty_state(False)
        return document

    @staticmethod
    def _reload_resolved_view(view, text, line_sources, root_path):
        """Перезалить текст вкладки, сохранив прокрутку и курсор."""
        widget = getattr(view, "editor", None)
        first_line = 0
        cursor = (0, 0)
        if widget is not None:
            try:
                first_line = widget.firstVisibleLine()
                cursor = widget.getCursorPosition()
            except Exception:
                pass

        view.set_content(text, line_sources, root_path)

        if widget is not None:
            try:
                widget.setFirstVisibleLine(first_line)
                widget.setCursorPosition(cursor[0], cursor[1])
            except Exception:
                pass

    def _persist_resolved_document(self, document):
        if not self.project.is_active:
            return False
        try:
            self.project.update_resolved_draft(
                document.tab_id,
                document.root_path,
                document.title,
                document.original_text,
                document.current_text,
                line_sources=document.line_sources,
            )
        except ProjectError as exc:
            self.log(
                "Проект: не удалось обновить resolved-черновик: %s" % exc
            )
            return False

        try:
            self._invalidate_paths([document.root_path])
        except Exception as exc:
            self.log("Проект: не удалось сбросить кэш резолва: %s" % exc)

        try:
            self._update_project_status()
        except Exception as exc:
            self.log("Проект: не удалось обновить статус: %s" % exc)
        return True

    def _save_resolved_document_state(self, document):
        if not self.project.is_active:
            self.set_status(
                "Создайте проект, чтобы сохранять изменения",
                7000,
            )
            return False
        try:
            self.project.save_resolved_draft(
                document.tab_id,
                document.current_text,
            )
        except ProjectError as exc:
            self.show_error(str(exc))
            return False

        if self.window is not None:
            try:
                self.window.editor.mark_tab_clean(document.tab_id)
                view = self.window.editor.get_tab(document.tab_id)
                if view is not None and hasattr(view, "mark_clean"):
                    view.mark_clean(document.current_text)
            except Exception as exc:
                self.log("Проект: не удалось обновить вкладку: %s" % exc)

        self.set_status("Resolved сохранён в проекте", 5000)
        self.log(
            "Проект: сохранён resolved-черновик %s" % document.tab_id
        )
        try:
            self._update_project_status()
        except Exception as exc:
            self.log("Проект: не удалось обновить статус: %s" % exc)
        return True

    def resolved_document(self, tab_id):
        """Живой документ по id вкладки (или ``None``)."""
        return self._resolved_documents.get(str(tab_id or ""))

    def _get_or_restore_document(self, tab_id, document_format="ltx"):
        """Документ из реестра или восстановленный из черновика проекта."""
        tab_id = str(tab_id or "")
        document = self._resolved_documents.get(tab_id)
        if document is not None:
            return document
        draft = (
            self.project.get_resolved_draft(tab_id)
            if self.project.is_active
            else None
        )
        if draft is None:
            return None
        return self._ensure_resolved_document(
            tab_id,
            draft.root_path,
            draft.title,
            draft.current_text,
            draft.line_sources,
            document_format=document_format,
        )

    def has_resolved_document(self, tab_id):
        return str(tab_id or "") in self._resolved_documents

    def resolved_document_title(self, tab_id):
        tab_id = str(tab_id or "")
        document = self._resolved_documents.get(tab_id)
        if document is not None:
            return document.title
        draft = (
            self.project.get_resolved_draft(tab_id)
            if self.project.is_active
            else None
        )
        return draft.title if draft is not None else ""

    def retain_resolved_document(self, tab_id):
        document = self._get_or_restore_document(tab_id)
        if document is not None:
            document.retain()
            return True
        return False

    def release_resolved_document(self, tab_id):
        tab_id = str(tab_id or "")
        document = self._resolved_documents.get(tab_id)
        if document is None:
            return False
        document.release()
        if document.can_drop():
            self._resolved_documents.pop(tab_id, None)
        return True

    def apply_resolved_line_edit(
        self,
        tab_id,
        action,
        line_number,
        text="",
    ):
        """Единственная точка правки resolved-документа из модуля."""
        document = self._get_or_restore_document(tab_id)
        if document is None:
            return False
        ok = document.apply_line_edit(action, line_number, text)
        if ok and self.window is not None:
            self.window.editor.set_tab_dirty(
                document.tab_id,
                document.is_dirty,
            )
        return ok

    def apply_resolved_field_edit(self, tab_id, section, key, value):
        document = self._get_or_restore_document(tab_id)
        if document is None:
            return False
        ok = document.apply_field_edit(section, key, value)
        if ok and self.window is not None:
            self.window.editor.set_tab_dirty(
                document.tab_id,
                document.is_dirty,
            )
        return ok

    def rename_resolved_field(
        self,
        tab_id,
        section,
        old_key,
        new_key,
        value,
    ):
        document = self._get_or_restore_document(tab_id)
        if document is None:
            return False
        ok = document.rename_field(
            section,
            old_key,
            new_key,
            value,
        )
        if ok and self.window is not None:
            self.window.editor.set_tab_dirty(
                document.tab_id,
                document.is_dirty,
            )
        return ok

    def undo_resolved_document(self, tab_id):
        document = self._get_or_restore_document(tab_id)
        return bool(document is not None and document.undo())

    def redo_resolved_document(self, tab_id):
        document = self._get_or_restore_document(tab_id)
        return bool(document is not None and document.redo())

    def resolved_section_at_line(self, tab_id, line_number):
        document = self._get_or_restore_document(tab_id)
        if document is None:
            return None
        return document.section_at_line(line_number)

    def item_entry(self, tab_id, section):
        """Текущая проекция предмета из документа (для карточки).

        Локализация читается вместе с черновиками проекта: отображаемый
        текст должен совпадать с XML, который будет сохранён в патч.
        """
        document = self._get_or_restore_document(tab_id)
        if document is None or self._forest is None:
            return None

        overlay = (
            self.project_overlay()
            if self.project.is_active
            else None
        )

        block = document.section_block(section)
        builder = getattr(
            self.services,
            "build_item_entry_from_block",
            None,
        )
        if block is not None and callable(builder):
            block_text, offset = block
            return _call_with_overlay(
                builder,
                self._forest,
                block_text,
                section,
                offset,
                overlay=overlay,
            )

        return _call_with_overlay(
            self.services.build_item_entry,
            self._forest,
            document.current_text,
            section,
            overlay=overlay,
        )

    def open_item_card_at_cursor(self):
        if self.window is None:
            return False
        view = self.window.editor.currentWidget()
        section = getattr(view, "section_at_cursor", None)
        if not callable(section):
            self.set_status(
                "Карточка предмета доступна только в resolved-вкладке",
                5000,
            )
            return False
        section_name = section()
        if not section_name:
            self.set_status("Курсор не находится в секции LTX", 5000)
            return False
        document = getattr(view, "document", None)
        tab_id = getattr(document, "tab_id", "")
        if not tab_id:
            return False
        return self.dispatch_action(
            Events.ITEM_CARD_REQUESTED,
            ItemCardRequest(
                tab_id=tab_id,
                section=section_name,
                line_number=getattr(view, "cursor_line", lambda: 0)(),
            ),
        )



    def resolved_draft(self, tab_id):
        # Живой документ — более свежий источник, чем проектный черновик:
        # он содержит несохранённые правки и ту же карту источников, что
        # видят все представления.
        document = self._resolved_documents.get(str(tab_id or ""))
        if document is not None:
            return document.snapshot()
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

        document = self._ensure_resolved_document(
            tab_id,
            root_path=root_path,
            title=title,
            text=current_text,
            line_sources=line_sources,
        )
        if document is None:
            return False

        if line_edit is not None:
            return self.apply_resolved_line_edit(
                tab_id,
                line_edit.action,
                line_edit.line_number,
                line_edit.text,
            )

        # Полная замена допустима, но не должна затирать более свежий
        # текст, если документ уже изменился из другой поверхности.
        if document.current_text == str(current_text or ""):
            return True
        return document.replace_text(
            current_text,
            line_sources=line_sources,
            atomic=True,
        )

    def sync_resolved_line_edit(self, tab_id, edit):
        """Точечно обновить открытую resolved-вкладку."""
        return self.apply_resolved_line_edit(
            tab_id,
            edit.action,
            edit.line_number,
            edit.text,
        )

    def sync_resolved_tab(self, tab_id, text, line_sources, root_path):
        """Обновить открытую resolved-вкладку без переключения на неё."""
        document = self._resolved_documents.get(str(tab_id or ""))
        if document is not None:
            document.replace_text(
                text,
                line_sources=line_sources,
                persist=False,
                atomic=False,
            )

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
        if document is not None:
            view.set_dirty_state(document.is_dirty)

        if widget is not None:
            try:
                widget.setFirstVisibleLine(first_line)
                widget.setCursorPosition(cursor[0], cursor[1])
            except Exception:
                pass


    def open_resolved_tab(self, tab_id, title, text, line_sources, root_path,
                          line_number=None):
        """Открыть (или сфокусировать) вкладку живого resolved-документа."""
        if self.window is None:
            return

        tab_id = str(tab_id or "")
        existing_widget = self.window.editor.get_tab(tab_id)
        from ..editor import ResolvedView

        if isinstance(existing_widget, ResolvedView):
            document = self._get_or_restore_document(tab_id)
            if document is None:
                document = self._ensure_resolved_document(
                    tab_id,
                    root_path=root_path,
                    title=title,
                    text=text,
                    line_sources=line_sources,
                )
            if document is not None:
                if existing_widget.document is not document:
                    existing_widget.bind_document(document)
                if (
                    not document.is_dirty
                    and not document.has_changes
                    and str(text) != document.current_text
                ):
                    if self.project.is_active:
                        self.project.remove_resolved_draft(tab_id)
                    document.reset(
                        text,
                        line_sources=line_sources,
                        saved_text=text,
                        original_text=text,
                        persist=False,
                    )
                    self._reload_resolved_view(
                        existing_widget,
                        document.current_text,
                        document.line_sources,
                        document.root_path,
                    )
                    existing_widget.set_dirty_state(False)
            self.window.editor.open_tab(
                tab_id,
                title,
                existing_widget,
                source_path=str(root_path),
            )
            if line_number is not None:
                existing_widget.goto_line(int(line_number))
            return

        document = self._ensure_resolved_document(
            tab_id,
            root_path=root_path,
            title=title,
            text=text,
            line_sources=line_sources,
        )
        if document is None:
            self.show_error(
                "Не удалось открыть resolved-документ: %s" % tab_id
            )
            return

        started = time.perf_counter()
        view = ResolvedView()
        view.set_editable(self.can_edit_documents())
        view.set_content(
            document.current_text,
            document.line_sources,
            document.root_path,
        )
        view.bind_document(document)
        view.set_dirty_state(document.is_dirty)
        content_elapsed = time.perf_counter() - started

        view.source_activated.connect(
            lambda path, search: self.request_open_file_with_search(path, search)
        )
        view.source_raw_activated.connect(
            lambda path, search: self.request_open_file_raw_with_search(
                path,
                search,
            )
        )
        view.diagnostic.connect(lambda msg: self.log("[resolved] %s" % msg))
        view.diagnostic.connect(lambda msg: self.set_status(msg, 8000))
        view.lexer_status.connect(self.on_lexer_status)
        view.save_requested.connect(
            lambda tid=tab_id: self.save_resolved_document(tid)
        )
        view.dirty_changed.connect(
            lambda dirty, tid=tab_id: self.window.editor.set_tab_dirty(
                tid,
                dirty,
            )
        )
        view.item_requested.connect(
            lambda section, tid=tab_id, v=view: self._request_item_card(
                tid,
                section,
                v.cursor_line(),
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

        if document.is_dirty:
            self.window.editor.set_tab_dirty(tab_id, True)

        if line_number is not None:
            view.goto_line(int(line_number))

    def _request_item_card(self, tab_id, section, line_number):
        self.dispatch_action(
            Events.ITEM_CARD_REQUESTED,
            ItemCardRequest(
                tab_id=str(tab_id),
                section=str(section),
                line_number=int(line_number or 0),
            ),
        )

    def open_resolved_document(self, tab_id, line_number=None):
        """Открыть или сфокусировать resolved-документ по id вкладки."""
        tab_id = str(tab_id or "")
        document = self._resolved_documents.get(tab_id)
        if document is None:
            draft = (
                self.project.get_resolved_draft(tab_id)
                if self.project.is_active
                else None
            )
            if draft is None:
                return False
            document = self._ensure_resolved_document(
                tab_id,
                draft.root_path,
                draft.title,
                draft.current_text,
                draft.line_sources,
            )
        if document is None:
            return False

        self.open_resolved_tab(
            tab_id,
            document.title,
            document.current_text,
            document.line_sources,
            document.root_path,
            line_number=line_number,
        )
        return True

    def open_xml_resolved_tab(self, tab_id, title, text, root_path,
                              line_sources=None, line_number=None):
        """Открыть XML-резолв в отдельной вкладке с панелью источников."""
        if self.window is None:
            return
        from ..editor import ResolvedView

        sources = list(line_sources or [])
        has_sources = any(src is not None for src in sources)
        started = time.perf_counter()

        document = self._get_or_restore_document(
            tab_id,
            document_format="xml",
        )
        if document is None:
            document = self._ensure_resolved_document(
                tab_id,
                root_path=root_path,
                title=title,
                text=text,
                line_sources=sources,
                document_format="xml",
            )
        if document is None:
            self.show_error(
                "Не удалось открыть XML-resolved-документ: %s" % tab_id
            )
            return

        existing_widget = self.window.editor.get_tab(tab_id)
        if isinstance(existing_widget, ResolvedView):
            if existing_widget.document is not document:
                existing_widget.bind_document(document)
            if (
                not document.is_dirty
                and not document.has_changes
                and str(text) != document.current_text
            ):
                if self.project.is_active:
                    # Свежий XML-резолв заменяет только чистый черновик.
                    try:
                        self.project.remove_resolved_draft(tab_id)
                    except Exception:
                        pass
                document.reset(
                    text,
                    line_sources=sources,
                    saved_text=text,
                    original_text=text,
                    persist=False,
                )
                self._reload_resolved_view(
                    existing_widget,
                    document.current_text,
                    document.line_sources,
                    document.root_path,
                )
            existing_widget.set_dirty_state(document.is_dirty)
            self.window.editor.open_tab(
                tab_id,
                title,
                existing_widget,
                source_path=str(root_path),
            )
            if line_number is not None:
                existing_widget.goto_line(int(line_number))
            return

        view = ResolvedView()
        view.set_editable(self.can_edit_documents())
        view.set_content(
            document.current_text,
            document.line_sources,
            str(document.root_path),
            sources_visible=has_sources,
        )
        view.bind_document(document)
        view.set_dirty_state(document.is_dirty)
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
        view.save_requested.connect(
            lambda tid=tab_id: self.save_resolved_document(tid)
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

        if document.is_dirty:
            self.window.editor.set_tab_dirty(tab_id, True)

        if line_number is not None:
            view.goto_line(int(line_number))

