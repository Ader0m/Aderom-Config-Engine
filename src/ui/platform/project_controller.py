# -*- coding: utf-8 -*-
"""Проектные операции PlatformApi.

Диалоги и файловые операции остаются в UI-слое, но отделены от
регистрации модулей и навигации.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import replace
from pathlib import Path

from PyQt5.QtWidgets import QFileDialog, QMessageBox

from sdk.contracts.events import Events, PLATFORM_OWNER
from sdk.features.projects import AssetDraftInfo, ProjectChanged, SavedDraft

from app.documents import ResetIntent
from app.projects import (
    ProjectAssetTypeError,
    ProjectError,
    ProjectSettingError,
)
from app.patching.commit import write_preview as write_preview_use_case
from app.projects import use_cases as project_use_cases
from ..tasks import current_task
from ..project_dialogs import (
    ProjectCreateDialog,
    ProjectLoadDialog,
    ProjectPreviewDialog,
    ProjectSettingsDialog,
)
from .feature_flags import patch_write_enabled


logger = logging.getLogger(__name__)


class ProjectControllerMixin:
    """Жизненный цикл проекта, сохранение и предпросмотр патча."""

    @staticmethod
    def _task_cancelled():
        """Отменена ли задача, выполняемая в текущем рабочем потоке."""
        handle = current_task()
        return bool(handle is not None and handle.is_cancelled)

    def is_project_active(self):
        return self.project.is_active

    def project_has_changes(self):
        return self.project.has_changes()

    def project_has_dirty(self):
        return self.project.has_dirty()

    def project_name(self):
        if not self.project.is_active:
            return ""
        return str(self.project.active.name or "")

    def project_overlay(self):
        """Единый слой правок активного проекта для VFS."""
        overlay = self.project.project_overlay()
        self.workspace.update_overlay(getattr(overlay, "revision", ""))
        return overlay

    def refresh_workspace(self):
        """Обновить виртуальные файлы проекта и вернуть (added, removed)."""
        return self._restore_project_virtual_files()

    def _invalidate_project_cache(self):
        """Инвалидировать только корни, затронутые проектом.

        Черновики и resolved-правки влияют на поддеревья своих корней.
        Если корень определить нельзя (или изменений нет), выполняется
        полная инвалидация: новый проект не должен унаследовать чужой
        точечный набор корней.
        """
        self._invalidate_paths(self.project.changed_paths())

    def _invalidate_saved_paths(self, paths):
        """Сбросить кеш резолва для файлов, записанных в проект.

        Вызывается только из путей сохранения (Ctrl+S, автосохранение,
        запись из карточки). Правка в редакторе кеш не трогает: ключ
        резолва и так учитывает overlay, а сброс на каждый символ
        запускал бы пересборку корня во время набора.
        """
        paths = [
            Path(path) for path in (paths or ()) if path is not None
        ]
        if not paths:
            return
        try:
            self._invalidate_paths(paths)
        except Exception as exc:
            self.log(
                "Проект: не удалось сбросить кэш после сохранения: %s"
                % exc
            )

    def _invalidate_saved(self, saved):
        """Сбросить кеш по событиям :class:`SavedDraft`."""
        self._invalidate_saved_paths(
            getattr(event, "path", None)
            for event in (saved or ())
            if getattr(event, "kind", "") in ("file", "resolved")
        )

    def _start_project_prebuild(self):
        """Пересобрать только затронутые проектом корни."""
        try:
            self.services.prebuild.ensure(
                overlay=self.project_overlay(),
                roots=getattr(self, "_project_prebuild_roots", None),
            )
        except Exception as exc:
            self.log(
                "Проект: не удалось запустить пересборку: %s" % exc
            )

    def workspace_forest(self):
        return self.workspace.forest_ref

    def workspace_generation(self):
        return self.workspace.generation

    def _restore_project_virtual_files(self):
        """Восстановить новые файлы проекта в текущем дереве VFS."""
        forest = self._forest
        if forest is None:
            return 0, 0

        overlay = self.project_overlay()
        added, removed = self.workspace.apply_overlay(
            overlay,
            services=self.services.tree,
        )
        if added or removed:
            self.publish_tree_ready(forest)
            try:
                roots = self.workspace.roots_for(
                    self.workspace.last_added_paths
                ) | set(self.workspace.last_removed_roots)
                if roots:
                    self.services.cache_control.invalidate_paths(roots)
                else:
                    self._invalidate_resolver_cache()
            except Exception:
                pass
            self.log(
                "Проект: VFS обновлён — добавлено файлов: %d, удалено: %d"
                % (added, removed)
            )
        elif self.workspace.is_stale:
            self.set_status(
                "Дерево устарело: требуется пересборка VFS",
            )
            self.log(
                "Проект: лес собран для другой ревизии overlay; "
                "виртуальные файлы не применены до пересборки VFS"
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
            self._invalidate_paths([path])
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

    def write_project_text_async(
        self,
        path,
        text,
        on_done=None,
        original_text=None,
    ):
        """Записать текстовый черновик в фоновом потоке.

        На один файл одновременно работает только одна запись. Если во
        время неё приходит новый текст, он ставится в очередь и
        записывается следующим. Сериализация/запись JSON выполняются в
        рабочем потоке; проект и вкладки обновляются в UI-потоке.
        """
        if not self.project.is_active:
            if callable(on_done):
                on_done(False, "Создайте проект, чтобы сохранять изменения")
            return False

        path = Path(path)
        key = self.project.path_key(path)
        request = {
            "path": path,
            "text": str(text or ""),
            "original_text": (
                None if original_text is None else str(original_text)
            ),
            "on_done": on_done,
        }

        if key in self._text_save_jobs:
            # Последняя версия текста заменит предыдущую после записи.
            self._text_save_pending[key] = request
            return True

        return self._start_text_save(key, request)

    def _start_text_save(self, key, request):
        path = Path(request["path"])
        try:
            target, payload, revision, saved_at = (
                self.project.stage_text_draft(
                    path,
                    request["text"],
                    original_text=request.get("original_text"),
                )
            )
        except Exception as exc:
            self.log(
                "Проект: не удалось подготовить сохранение %s: %s"
                % (path.name, exc)
            )
            callback = request.get("on_done")
            if callable(callback):
                callback(False, str(exc))
            return False

        job = dict(request)
        job.update(
            target=target,
            payload=payload,
            revision=revision,
            saved_at=saved_at,
            project=self.project.active,
        )
        self._text_save_jobs[key] = job

        def work(_log):
            self.project.write_staged_draft(
                path,
                target,
                payload,
                revision,
            )
            return target

        def finish(_result, error):
            self._finish_text_save(key, error)

        run = getattr(self.tasks, "run", None)
        if not callable(run):
            try:
                work(None)
            except Exception as exc:
                self._finish_text_save(key, exc)
            else:
                self._finish_text_save(key, None)
            return True

        try:
            run(
                work,
                on_done=finish,
                operation_key="project.text-save:%s" % key,
                title="Сохранение текста: %s" % path.name,
            )
        except Exception as exc:
            self._text_save_jobs.pop(key, None)
            self.log(
                "Проект: не удалось запустить сохранение %s: %s"
                % (path.name, exc)
            )
            callback = request.get("on_done")
            if callable(callback):
                callback(False, str(exc))
            return False
        return True

    def _finish_text_save(self, key, error):
        job = self._text_save_jobs.pop(key, None)
        if job is None:
            return

        path = Path(job["path"])
        message = None if error is None else str(error)
        ok = False

        if error is None:
            if not self.project.is_active:
                message = "Проект закрыт во время сохранения"
            elif self.project.active is not job.get("project"):
                message = "Во время сохранения открыт другой проект"
            elif self.project.mark_draft_saved(
                path,
                job["revision"],
                job["saved_at"],
            ):
                ok = True
            else:
                message = "Черновик изменён во время сохранения"

        if ok:
            if self.window is not None:
                editor = getattr(self.window, "editor", None)
                if editor is not None:
                    try:
                        editor.mark_clean(path, job["text"])
                    except Exception:
                        pass
            try:
                self._invalidate_paths([path])
            except Exception as exc:
                self.log(
                    "Проект: не удалось сбросить кэш после %s: %s"
                    % (path.name, exc)
                )
            try:
                self._update_project_status()
            except Exception:
                pass
            self.log("Проект: сохранён текст %s" % path.name)
        elif message:
            self.log(
                "Проект: ошибка сохранения %s: %s"
                % (path.name, message)
            )

        callback = job.get("on_done")
        if callable(callback):
            try:
                callback(ok, message)
            except Exception:
                logger.exception(
                    "Ошибка обработчика фонового сохранения %s",
                    path,
                )

        pending = self._text_save_pending.pop(key, None)
        if (
            pending is not None
            and self.project.is_active
            and self.project.active is job.get("project")
        ):
            self._start_text_save(key, pending)

    def write_project_texts(self, files):
        """Атомарно записать несколько черновиков проекта.

        Сначала сохраняются все файлы. Если хотя бы один не записался,
        уже сохранённые возвращаются к предыдущему состоянию.
        """
        if not self.project.is_active:
            self.set_status(
                "Создайте проект, чтобы создавать файлы",
                7000,
            )
            return False

        staged = []
        for path, text in files:
            path = Path(path)
            draft = self.project.get_draft(path)
            staged.append(
                (
                    path,
                    replace(draft) if draft is not None else None,
                )
            )

        try:
            for path, text in files:
                path = Path(path)
                self.project.save_draft(path, text)
        except ProjectError as exc:
            for path, snapshot in staged:
                try:
                    if snapshot is not None:
                        self.project.restore_draft(snapshot)
                    else:
                        self.project.remove_draft(path)
                except Exception:
                    self.log(
                        "Проект: не удалось откатить %s" % path
                    )
            self.show_error(
                "Не удалось записать файлы проекта:\n%s\n"
                "Изменения отменены." % exc
            )
            return False

        try:
            self._invalidate_paths([path for path, _ in files])
        except Exception as exc:
            self.log("Проект: не удалось сбросить кэш резолва: %s" % exc)

        if self.window is not None:
            editor = getattr(self.window, "editor", None)
            if editor is not None:
                for path, text in files:
                    try:
                        editor.mark_clean(path, text)
                    except Exception:
                        pass

        self._update_project_status()
        return True

    def register_project_asset_type(self, asset_type, owner=None):
        """Зарегистрировать тип двоичного ресурса от имени модуля."""
        try:
            registered = self.asset_types.register(
                asset_type,
                owner=owner,
            )
        except ProjectAssetTypeError as exc:
            self.log("Проект: тип ресурса не зарегистрирован: %s" % exc)
            return False

        self.project.invalidate_preview_cache()
        self.log(
            "Проект: зарегистрирован тип ресурса %s (%s/%s)"
            % (
                registered.key,
                registered.vfs_root,
                ", ".join(registered.extensions),
            )
        )
        return True

    def unregister_project_asset_type(self, key, owner=None):
        if not self.asset_types.unregister(key, owner=owner):
            return False
        self.project.invalidate_preview_cache()
        self.log("Проект: тип ресурса снят: %s" % key)
        return True

    def project_asset_types(self):
        return self.asset_types.types()

    # -------------------------------------------------- настройки модулей
    def register_project_setting(self, setting, owner=None):
        """Зарегистрировать настройку проекта от имени модуля."""
        try:
            registered = self.project_setting_registry.register(
                setting,
                owner=owner,
            )
        except ProjectSettingError as exc:
            self.log("Проект: настройка не зарегистрирована: %s" % exc)
            return False

        self.project.invalidate_preview_cache()
        self.log(
            "Проект: зарегистрирована настройка %s (%s)"
            % (registered.key, registered.label)
        )
        return True

    def unregister_project_setting(self, key, owner=None):
        if not self.project_setting_registry.unregister(key, owner=owner):
            return False
        self.project.invalidate_preview_cache()
        self.log("Проект: настройка снята: %s" % key)
        return True

    def project_setting_definitions(self):
        """Описания настроек, которые показываются в окне проекта."""
        return self.project_setting_registry.settings()

    def project_setting(self, key, default=None):
        """Текущее значение настройки активного проекта."""
        setting = self.project_setting_registry.get(key)
        fallback = (
            default
            if default is not None
            else (setting.default if setting is not None else None)
        )
        if not self.project.is_active:
            return fallback
        value = self.project.module_setting(key, fallback)
        if setting is None:
            return value
        try:
            return self.project_setting_registry.normalize(key, value)
        except ProjectSettingError:
            return fallback

    def set_project_setting(self, key, value):
        """Сохранить значение настройки в активном проекте."""
        setting = self.project_setting_registry.get(key)
        if setting is None:
            self.log("Проект: неизвестная настройка %s" % key)
            return False

        try:
            normalized = self.project_setting_registry.normalize(key, value)
            self.project.set_module_setting(key, normalized)
        except (ProjectSettingError, ProjectError) as exc:
            self.log("Проект: настройка %s не изменена: %s" % (key, exc))
            return False

        self.events.publish(
            Events.PROJECT_SETTINGS_CHANGED,
            {setting.key: normalized},
        )
        self.log(
            "Проект: настройка %s = %r" % (setting.key, normalized)
        )
        return True

    def register_file_reader(self, reader):
        try:
            subscription = self.file_readers.register(reader)
        except TypeError as exc:
            self.log("Проект: читатель не зарегистрирован: %s" % exc)
            return None
        self.log(
            "Проект: зарегистрирован читатель %s"
            % type(reader).__name__
        )
        return subscription

    def read_file_view(self, path, data=None):
        return self.file_readers.read(path, data)

    def read_project_asset(self, path):
        """Текущие байты двоичного черновика активного проекта."""
        if not self.project.is_active:
            return None
        draft = self.project.get_asset_draft(path)
        return draft.current_bytes if draft is not None else None

    def update_project_asset(self, path, data):
        """Обновить двоичный черновик в памяти."""
        if not self.project.is_active:
            self.set_status(
                "Создайте проект, чтобы изменять ресурсы",
                7000,
            )
            return False

        try:
            self.project.update_asset_draft(path, data)
        except ProjectError as exc:
            self.show_error(str(exc))
            return False

        self._update_project_status()
        return True

    def save_project_asset(self, path, data=None):
        """Записать двоичный черновик в каталог проекта сейчас."""
        if not self.project.is_active:
            self.set_status(
                "Создайте проект, чтобы сохранять ресурсы",
                7000,
            )
            return False

        try:
            draft = self.project.save_asset_draft(path, data)
        except ProjectError as exc:
            self.show_error(str(exc))
            return False

        saved = SavedDraft(
            kind="asset",
            key=self.project.path_key(draft.path),
            path=draft.path,
        )
        self.events.publish(Events.PROJECT_SAVED, saved)
        self.set_status(
            "Сохранено в проекте: %s" % Path(path).name,
            5000,
        )
        self.log("Проект: сохранён ресурс %s" % Path(path).name)
        self._update_project_status()
        return True

    def project_asset_drafts(self):
        """Двоичные черновики активного проекта."""
        if not self.project.is_active:
            return ()
        return tuple(
            AssetDraftInfo(
                path=draft.path,
                is_new=bool(draft.is_new),
                changed=bool(draft.has_changes),
                dirty=bool(draft.is_dirty),
                size=int(draft.size),
            )
            for draft in self.project.active.assets.values()
        )

    def set_editor_tab_dirty(self, tab_id, dirty, owner=None):
        if self.window is None:
            return False
        editor = getattr(self.window, "editor", None)
        setter = getattr(editor, "set_tab_dirty", None)
        if callable(setter):
            if owner is None:
                return bool(setter(tab_id, bool(dirty)))
            return bool(setter(tab_id, bool(dirty), owner=owner))
        return False

    def set_editor_tab_busy(self, tab_id, busy, owner=None):
        if self.window is None:
            return False
        editor = getattr(self.window, "editor", None)
        setter = getattr(editor, "set_tab_busy", None)
        if callable(setter):
            if owner is None:
                return bool(setter(str(tab_id or ""), bool(busy)))
            return bool(setter(str(tab_id or ""), bool(busy), owner=owner))
        return False

    def _flush_editor_pending(self):
        """Перенести отложенный текст вкладок в проектные черновики."""
        if self.window is None:
            return
        editor = getattr(self.window, "editor", None)
        flush = getattr(editor, "flush_pending", None)
        if callable(flush):
            try:
                flush()
            except Exception:
                logger.exception(
                    "Не удалось зафиксировать отложенный текст вкладок"
                )

    def save_project_now(self):
        if not self.project.is_active:
            self.set_status(
                "Создайте проект, чтобы сохранять изменения",
                7000,
            )
            return 0

        self._flush_editor_pending()
        try:
            saved = self.project.flush()
        except Exception as exc:
            self.log("Проект: ошибка сохранения: %s" % exc)
            return 0

        self._invalidate_saved(saved)

        if self.window is not None:
            for event in saved:
                if event.kind == "file":
                    # Текст в событии больше не передаётся: перед
                    # автосохранением вкладки уже синхронизированы.
                    self.window.editor.mark_clean(event.path)
                elif event.kind == "resolved":
                    self.window.editor.mark_tab_clean(event.key)
                    document = self._resolved_documents.get(str(event.key))
                    if document is not None:
                        document.mark_saved()
                        view = self.window.editor.get_tab(document.tab_id)
                        if view is not None and hasattr(
                            view,
                            "set_dirty_state",
                        ):
                            if hasattr(view, "mark_clean"):
                                view.mark_clean(document.saved_text)
                            else:
                                view.set_dirty_state(False)

        for event in saved:
            self.events.publish(Events.PROJECT_SAVED, event)

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
                return self.services.tree.read_xml_text(path)
            return self.services.tree.read_text(path)
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
        except Exception:
            logger.exception("Не удалось обновить черновик %s", path)
            self.log("Проект: непредвиденная ошибка черновика (crash.log)")
            return

        # Кеш здесь намеренно не сбрасывается: черновик живёт в памяти,
        # а ключ резолва уже включает overlay проекта. Инвалидация — и,
        # значит, пересборка корня — выполняется только при сохранении.
        self._update_project_status()

    def save_resolved_document(self, tab_id, view=None):
        tab_id = str(tab_id or "")
        document = self._resolved_documents.get(tab_id)
        if document is not None:
            if document.save():
                self.set_status("Resolved сохранён в проекте", 5000)
            else:
                self.set_status(
                    "Не удалось сохранить resolved-документ",
                    6000,
                )
            return

        if not self.project.is_active:
            self.set_status(
                "Создайте проект, чтобы сохранять изменения",
                7000,
            )
            return

        # Ctrl+S из resolved-вкладки приходит без view. Живого документа
        # может уже не быть (смена проекта), но черновик мог сохраниться.
        draft = self.project.get_resolved_draft(tab_id)
        if draft is None:
            self.set_status(
                "Нет resolved-черновика для сохранения",
                5000,
            )
            self.log(
                "Проект: нет resolved-черновика %s — сохранять нечего"
                % tab_id
            )
            return

        # Источник истины — черновик: он обновляется при каждой правке
        # живым документом. Текст «осиротевшей» вкладки может быть старее,
        # поэтому без живого документа его не используем.
        text = draft.current_text

        try:
            self.project.save_resolved_draft(tab_id, text)
        except ProjectError as exc:
            self.show_error(str(exc))
            return
        except Exception:
            logger.exception(
                "Не удалось сохранить resolved-черновик %s",
                tab_id,
            )
            self.show_error("Не удалось сохранить resolved-черновик")
            return

        if self.window is not None:
            self.window.editor.mark_tab_clean(tab_id)

        self._invalidate_saved_paths([draft.root_path])

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
        except Exception:
            logger.exception("Не удалось сохранить файл %s", path)
            self.show_error("Не удалось сохранить файл проекта")
            return

        if self.window is not None:
            self.window.editor.mark_clean(path, text)

        self._invalidate_saved_paths([path])

        self.set_status(
            "Сохранено в проекте: %s" % Path(path).name,
            5000,
        )
        self.log("Проект: сохранён черновик %s" % Path(path).name)
        self._update_project_status()

    def _set_project_loading(self, loading):
        """Заблокировать правку и повесить «часики» на вкладки.

        Пока проект активируется, открытые документы не должны
        редактироваться. Иконка снимается ровно тогда, когда правка
        снова разрешена.
        """
        if self.window is None:
            return
        editor = getattr(self.window, "editor", None)
        if editor is None:
            return

        set_editable = getattr(editor, "set_editable", None)
        if callable(set_editable):
            set_editable(
                False if loading else self.can_edit_documents()
            )

        set_busy = getattr(editor, "set_busy", None)
        if callable(set_busy):
            set_busy(bool(loading))

        # Без активного проекта «часики» означали бы вечную загрузку:
        # файлы просто доступны только для чтения.
        if not loading and not self.can_edit_documents():
            clear_busy = getattr(editor, "clear_tab_busy", None)
            if callable(clear_busy):
                clear_busy()

        # Иконки нужно показать до синхронной загрузки: иначе Qt
        # перерисует вкладки только после её завершения.
        tab_bar = getattr(editor, "tabBar", None)
        if callable(tab_bar):
            try:
                tab_bar().repaint()
            except Exception:
                pass

    def _begin_project_activation(self):
        # Правка, ещё не доехавшая из редактора, относится к старому
        # проекту: фиксируем её до close()/load().
        self._flush_editor_pending()
        # Старый autosave не должен конкурировать за файлы и события:
        # просим его остановиться; epoch в ProjectService.close()
        # гарантирует, что он не запишет старый снимок после flush().
        project = self.project.active
        if project is not None:
            try:
                self.tasks.cancel_owner(project.id)
            except Exception:
                pass
        self._autosave_pending = False
        self._autosave_pending_project = None
        self._set_project_loading(True)

    def _end_project_activation(self):
        self._set_project_loading(False)

    # -------------------------------------------------- сессия вкладок
    def _editor_widget(self):
        if self.window is None:
            return None
        return getattr(self.window, "editor", None)

    def _save_open_tabs(self):
        """Сохранить открытые вкладки в каталог активного проекта."""
        if not self.project.is_active:
            return False
        editor = self._editor_widget()
        collector = getattr(editor, "session_tabs", None)
        saver = getattr(self.project, "save_tabs", None)
        if not callable(collector) or not callable(saver):
            return False

        try:
            saver(collector())
        except Exception:
            logger.exception("Не удалось сохранить открытые вкладки")
            self.log("Проект: не удалось сохранить открытые вкладки")
            return False
        return True

    def _reset_editor_tabs(self):
        """Закрыть вкладки прошлого проекта перед восстановлением новых."""
        self.close_section_cards()
        editor = self._editor_widget()
        closer = getattr(editor, "close_all_tabs", None)
        if not callable(closer):
            return False
        try:
            closer()
        except Exception:
            logger.exception("Не удалось закрыть вкладки прошлого проекта")
            return False
        return True

    def _restore_editor_tabs(self):
        """Восстановить вкладки, сохранённые в активном проекте."""
        editor = self._editor_widget()
        if editor is None:
            return

        loader = getattr(self.project, "load_tabs", None)
        try:
            records = loader() if callable(loader) else []
        except Exception:
            logger.exception("Не удалось прочитать вкладки проекта")
            records = []

        if not records:
            ensure = getattr(editor, "ensure_default_tab", None)
            if callable(ensure):
                ensure()
            return

        active_id = None
        for record in records:
            if not isinstance(record, dict):
                continue
            tab_id = str(record.get("tab_id") or "")
            if not tab_id:
                continue

            try:
                self._restore_editor_tab(record)
            except Exception:
                logger.exception(
                    "Не удалось восстановить вкладку %s",
                    tab_id,
                )
                self._open_missing_owner_tab(
                    tab_id,
                    record.get("title") or tab_id,
                    record.get("owner"),
                )

            if record.get("active"):
                active_id = tab_id

        if active_id:
            select = getattr(editor, "set_current_tab", None)
            if callable(select):
                select(active_id)

    def _restore_editor_tab(self, record):
        kind = str(record.get("kind") or "module").lower()
        if kind == "editor":
            self._restore_file_tab(record)
        elif kind == "resolved":
            self._restore_resolved_tab(record)
        else:
            self._restore_module_tab(record)

    def _restore_file_tab(self, record):
        editor = self._editor_widget()
        if editor is None:
            return

        source_path = record.get("source_path")
        tab_id = str(record.get("tab_id") or "")
        title = str(record.get("title") or "")
        owner = record.get("owner") or None
        if tab_id == "ui.editor":
            # Старые проекты писали сюда owner "ui"; default-вкладка
            # всегда принадлежит платформе.
            owner = None
        state = record.get("state")
        if not isinstance(state, dict):
            state = {}

        if source_path:
            text = self.document_text(source_path)
            label = str(state.get("label") or title or source_path)
            shower = getattr(editor, "show_text", None)
            if callable(shower):
                shower(
                    text,
                    label,
                    readonly=bool(state.get("readonly", False)),
                    source_path=source_path,
                    notice=state.get("notice") or None,
                    tab_id=tab_id or None,
                    owner=owner,
                )
            else:
                opener = getattr(editor, "open_file", None)
                if callable(opener):
                    opener(source_path, text=text)
        else:
            ensure = getattr(editor, "ensure_default_tab", None)
            if callable(ensure):
                ensure()

        if title:
            setter = getattr(editor, "set_tab_title", None)
            if callable(setter):
                setter(record.get("tab_id"), title)

    def _restore_resolved_tab(self, record):
        editor = self._editor_widget()
        tab_id = str(record.get("tab_id") or "")
        owner = record.get("owner") or None

        if self.project.is_active:
            draft = self.project.get_resolved_draft(tab_id)
            if draft is not None:
                opener = getattr(self, "open_resolved_document", None)
                if callable(opener) and opener(tab_id, owner=owner):
                    self._restore_tab_title(editor, record)
                    return True

        source_path = record.get("source_path")
        if source_path:
            # Текст resolved-вкладки не хранится отдельно: при отсутствии
            # черновика собираем его заново штатным обработчиком файла.
            self.request_open_file(source_path)
            self._restore_tab_title(editor, record)
            return True

        return False

    def _restore_module_tab(self, record):
        editor = self._editor_widget()
        tab_id = str(record.get("tab_id") or "")
        title = str(record.get("title") or tab_id)

        requested = bool(self.request_tab(tab_id))
        has_tab = getattr(editor, "has_tab", None)
        opened = bool(callable(has_tab) and has_tab(tab_id))
        if requested or opened:
            self._restore_tab_title(editor, record)
            return

        self._open_missing_owner_tab(
            tab_id,
            title,
            record.get("owner"),
        )

    def _restore_tab_title(self, editor, record):
        setter = getattr(editor, "set_tab_title", None)
        title = record.get("title")
        if callable(setter) and title:
            setter(record.get("tab_id"), str(title))

    def _open_missing_owner_tab(self, tab_id, title, owner=None):
        editor = self._editor_widget()
        opener = getattr(editor, "open_missing_owner_tab", None)
        if callable(opener):
            opener(tab_id, title, owner=owner)

    def _activate_project_state(self, project, message):
        """Привести VFS и вкладки к состоянию активированного проекта.

        Вызывается между :meth:`_begin_project_activation` и
        :meth:`_end_project_activation`, пока правка ещё заблокирована.
        """
        self._restore_project_virtual_files()
        self._invalidate_project_cache()
        self._update_project_status()
        # Сначала освобождаем старые живые документы, затем заново
        # привязываем открытые вкладки к черновикам нового проекта.
        self.events.publish(
            Events.PROJECT_CHANGED,
            ProjectChanged(
                project_id=str(getattr(project, "id", "") or ""),
                name=str(getattr(project, "name", "") or ""),
                active=True,
            ),
        )
        # Вкладки принадлежат конкретному проекту: старые закрываем,
        # сохранённые в проекте — восстанавливаем ниже.
        self._reset_editor_tabs()
        try:
            self._reload_open_documents()
        except Exception:
            logger.exception("Не удалось обновить открытые документы")
            self.log(
                "Проект: не удалось обновить открытые документы "
                "(crash.log)"
            )
        self._start_project_prebuild()
        self._restore_editor_tabs()
        self.log(message)
        self.set_status(message, 5000)

    def _require_project_tree(self):
        """Проектные операции требуют уже собранный VFS-лес.

        На старте приложения сканирование идёт в фоне. Если открыть
        проект раньше, его виртуальные файлы не попадут в лес.
        """
        if self.has_forest():
            return True
        if self.operations.is_active("vfs.rebuild"):
            self.show_error(
                "Дерево строится: дождитесь окончания сборки VFS."
            )
        else:
            self.show_error(
                "Дерево не построено: дождитесь окончания сборки."
            )
        return False

    def _project_tree_dependency(self):
        """Зависимость проектной операции от построения VFS.

        Активная пересборка важна даже при наличии старого forest: он
        может быть снимком до изменений проекта/модов. ``None`` — дерева
        нет и сборка не идёт; ``()`` — дерево готово и не пересобирается;
        иначе — ключ операции, после которой можно продолжать.
        """
        if self.operations.is_active("vfs.rebuild"):
            return ("vfs.rebuild",)
        if self.has_forest():
            return ()
        return None

    def _project_tree_dependency_provider(self):
        """Callable для атомарной проверки зависимости в TaskService."""
        return lambda: self._project_tree_dependency() or ()

    def create_project(self):
        if self.window is None:
            return
        if self.operations.is_active("project.create"):
            self.set_status("Создание проекта уже выполняется", 5000)
            self.log("Проект: создание уже выполняется")
            return

        dependency = self._project_tree_dependency()
        if dependency is None:
            self._require_project_tree()
            return

        setting_definitions = self.project_setting_registry.settings()
        setting_values = {
            setting.key: setting.default
            for setting in setting_definitions
        }
        dialog = ProjectCreateDialog(
            self.window,
            settings=setting_definitions,
            setting_values=setting_values,
            base_dir=self.project.base_dir,
            suggest_dir=self.project.suggest_project_dir,
        )
        if not dialog.exec_():
            return

        name = dialog.project_name()
        project_dir = dialog.project_directory() or None
        save_mode = dialog.save_mode()
        xml_patch_mode = dialog.xml_patch_mode()
        module_settings = dict(dialog.module_setting_values())

        dependency = self._project_tree_dependency()
        if dependency is None:
            self._require_project_tree()
            return

        self._save_open_tabs()
        activation_started = [False]
        activation_ready = threading.Event()

        def on_log(message):
            if not activation_started[0]:
                activation_started[0] = True
                try:
                    self._begin_project_activation()
                finally:
                    activation_ready.set()
            self.log(message)

        def work(log):
            log("Создание проекта: подготовка")
            if not activation_ready.wait(5):
                raise RuntimeError(
                    "UI не подтвердил начало активации проекта"
                )
            return self.project.create(
                name,
                [],
                save_mode,
                xml_patch_mode=xml_patch_mode,
                project_dir=project_dir,
            )

        def done(project, error):
            try:
                if error is not None:
                    if not getattr(self, "_closing", False):
                        self.set_status("Ошибка создания проекта")
                        self.log("Проект: ошибка создания: %s" % error)
                        self.show_error(str(error))
                    return
                if getattr(self, "_closing", False):
                    # Окно уже закрывается: не собираем UI нового
                    # проекта, но созданный проект останется на диске и
                    # будет сохранён при повторном close.
                    return
                for key, value in module_settings.items():
                    self.set_project_setting(key, value)
                self._activate_project_state(
                    project,
                    "Проект создан: %s" % project.name,
                )
            finally:
                if activation_started[0]:
                    self._end_project_activation()
                if getattr(self, "_closing", False):
                    close = getattr(self.window, "close", None)
                    if callable(close):
                        close()

        if dependency:
            self.set_status("Создание проекта: ожидание сборки VFS…")

        try:
            self.tasks.run(
                work,
                on_done=done,
                on_log=on_log,
                operation_key="project.create",
                title="Создание проекта",
                depends_on=self._project_tree_dependency_provider(),
                owner=PLATFORM_OWNER,
            )
        except Exception as exc:
            if activation_started[0]:
                self._end_project_activation()
            self.show_error(
                "Не удалось запустить создание проекта: %s" % exc
            )

    def load_project(self):
        """Загрузить проект в фоне.

        Поиск сохранённых проектов и сама загрузка идут через
        `TaskService`: UI-поток не сканирует каталоги и не читает
        `project.json`. `operation_key='project.load'` защищает от
        параллельных загрузок, `project.list` — от параллельного поиска.
        """
        if getattr(self, "_closing", False):
            return
        if self.window is None:
            return
        if self.operations.is_active("project.load"):
            self.set_status("Загрузка проекта уже выполняется", 5000)
            self.log("Проект: загрузка уже выполняется")
            return
        if self.operations.is_active("project.list"):
            self.set_status("Поиск проектов уже выполняется", 5000)
            self.log("Проект: поиск уже выполняется")
            return

        dependency = self._project_tree_dependency()
        if dependency is None:
            self._require_project_tree()
            return

        canceled = [False]

        def work(_log):
            projects = self.project.list_projects(
                should_stop=self._task_cancelled,
            )
            if self._task_cancelled():
                canceled[0] = True
            return projects

        def done(projects, error):
            if canceled[0] or getattr(self, "_closing", False):
                return
            if error is not None:
                self.set_status("Ошибка поиска проектов")
                self.log("Проект: ошибка поиска: %s" % error)
                self.show_error(
                    "Не удалось получить список проектов: %s" % error
                )
                return
            self._open_project_load_dialog(projects)

        run = getattr(self.tasks, "run", None)
        if not callable(run):
            # Встраиваемый контекст без TaskService: сохраняем прежний
            # синхронный путь, в штатном приложении он не используется.
            self._open_project_load_dialog(self.project.list_projects())
            return

        self.set_status("Поиск проектов…")
        try:
            run(
                work,
                on_done=done,
                owner=PLATFORM_OWNER,
                operation_key="project.list",
                title="Поиск проектов",
            )
        except Exception as exc:
            self.set_status("Ошибка поиска проектов")
            self.log("Проект: не удалось запустить поиск: %s" % exc)
            self.show_error(
                "Не удалось запустить поиск проектов: %s" % exc
            )

    def _open_project_load_dialog(self, projects):
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

        dependency = self._project_tree_dependency()
        if dependency is None:
            self._require_project_tree()
            return

        current = self.project.active
        owner = (
            current.id if current is not None else PLATFORM_OWNER
        )
        self._save_open_tabs()
        self._begin_project_activation()
        if dependency:
            self.set_status(
                "Загрузка проекта: ожидание сборки VFS…",
            )

        def work(log):
            return self.project.load(project_id)

        def done(project, error):
            try:
                if getattr(self, "_closing", False):
                    # Окно уже закрывается: не показываем ошибку и не
                    # перестраиваем UI под новый проект. Сам проект уже
                    # активирован ProjectService.load; закрытие сохранит
                    # его.
                    return
                if error is not None:
                    self.set_status("Ошибка загрузки проекта")
                    self.log(
                        "Проект: ошибка загрузки: %s" % error
                    )
                    self.show_error(str(error))
                    return
                if not self.has_forest():
                    self.show_error(
                        "Дерево не построено: проект не активирован."
                    )
                    return
                self._activate_project_state(
                    project,
                    "Проект загружен: %s" % project.name,
                )
            finally:
                self._end_project_activation()
                if getattr(self, "_closing", False):
                    close = getattr(self.window, "close", None)
                    if callable(close):
                        close()

        try:
            self.tasks.run(
                work,
                on_done=done,
                on_log=self.log,
                operation_key="project.load",
                title="Загрузка проекта",
                depends_on=self._project_tree_dependency_provider(),
                owner=owner,
            )
        except Exception as exc:
            self._end_project_activation()
            self.show_error(
                "Не удалось запустить загрузку проекта: %s" % exc
            )

    def _delete_project(self):
        project = self.project.active
        if project is None:
            return False
        operation_key = "project.delete:%s" % project.id
        if self.operations.is_active(operation_key):
            self.set_status("Удаление проекта уже выполняется", 5000)
            self.log("Проект: удаление уже выполняется")
            return True

        self._begin_project_activation()

        def work(_log):
            return self.project.delete_active_project(
                expected_project=project,
            )

        def done(_result, error):
            try:
                if error is not None:
                    self.set_status("Ошибка удаления проекта")
                    self.log(
                        "Проект: ошибка удаления: %s" % error
                    )
                    self.show_error(str(error))
                    return
                self._after_project_deleted()
            finally:
                self._end_project_activation()
                if getattr(self, "_closing", False):
                    close = getattr(self.window, "close", None)
                    if callable(close):
                        close()

        run = getattr(self.tasks, "run", None)
        if not callable(run):
            # Встраиваемый контекст без TaskService: прежний синхронный
            # путь. В штатном приложении удаление идёт задачей.
            try:
                try:
                    self.project.delete_active_project(
                        expected_project=project,
                    )
                except ProjectError as exc:
                    self.show_error(str(exc))
                    return False
                self._after_project_deleted()
                return True
            finally:
                self._end_project_activation()

        try:
            run(
                work,
                on_done=done,
                owner=project.id,
                operation_key=operation_key,
                title="Удаление проекта",
            )
        except Exception as exc:
            self._end_project_activation()
            self.show_error(
                "Не удалось запустить удаление проекта: %s" % exc
            )
            return False
        self.set_status("Удаление проекта…")
        return True

    def _after_project_deleted(self):
        self._restore_project_virtual_files()
        self._invalidate_resolver_cache()
        self._update_project_status()
        self.events.publish(
            Events.PROJECT_CHANGED,
            ProjectChanged(active=False),
        )
        self._reset_editor_tabs()
        editor = self._editor_widget()
        ensure = getattr(editor, "ensure_default_tab", None)
        if callable(ensure):
            ensure()
        self.log("Проект удалён")
        self.set_status("Проект удалён", 5000)

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

        setting_definitions = self.project_setting_registry.settings()
        setting_values = {
            setting.key: self.project_setting(setting.key)
            for setting in setting_definitions
        }
        dialog = ProjectSettingsDialog(
            self.project.active,
            self.window,
            on_delete=self._delete_project,
            settings=setting_definitions,
            setting_values=setting_values,
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

        for key, value in dialog.module_setting_values().items():
            self.set_project_setting(key, value)

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
        self._flush_editor_pending()
        editor = getattr(self.window, "editor", None)
        if editor is None:
            return

        for path in editor.open_paths():
            text = self.document_text(path)
            editor.reload_path(path, text)
            editor.mark_clean(path, text)

        handled = set()
        for tab_id, document in list(self._resolved_documents.items()):
            draft = self.project.get_resolved_draft(tab_id)
            if draft is None:
                continue
            document.apply(
                ResetIntent(
                    draft.current_text,
                    line_sources=draft.line_sources,
                    saved_text=draft.saved_text,
                    original_text=draft.original_text,
                    persist=False,
                )
            )
            view = editor.get_tab(tab_id)
            if view is not None and hasattr(view, "set_content"):
                view.set_content(
                    document.current_text,
                    document.line_sources,
                    document.root_path,
                )
                view.set_dirty_state(document.is_dirty)
                set_tab_busy = getattr(editor, "set_tab_busy", None)
                if callable(set_tab_busy):
                    set_tab_busy(tab_id, False)
            editor.set_tab_dirty(tab_id, document.is_dirty)
            handled.add(tab_id)

        for tab_id, view in editor.resolved_tabs():
            if tab_id in handled:
                continue
            draft = self.project.get_resolved_draft(tab_id)
            if draft is None:
                unbind = getattr(view, "unbind_document", None)
                if callable(unbind):
                    unbind()
                # Без черновика правки сохранить некуда: оставляем
                # вкладку только для чтения, иначе пользователь
                # редактирует текст, который молча потеряется.
                supported = getattr(
                    view,
                    "set_editing_supported",
                    None,
                )
                if callable(supported):
                    supported(False)
                set_editable = getattr(view, "set_editable", None)
                if callable(set_editable):
                    set_editable(False)
                set_tab_busy = getattr(editor, "set_tab_busy", None)
                if callable(set_tab_busy):
                    set_tab_busy(tab_id, True)
                # Черновика в новом проекте нет, но сам корень может
                # принадлежать ему: соберём вкладку заново в фоне, чтобы
                # она стала проектной, а не осталась «устаревшей».
                refresh = getattr(
                    self,
                    "_refresh_orphan_resolved_tab",
                    None,
                )
                if callable(refresh):
                    refresh(tab_id, view)
                editor.set_tab_dirty(tab_id, False)
                continue

            document_format = (
                "xml"
                if Path(draft.root_path).suffix.lower() == ".xml"
                else "ltx"
            )
            document = self._ensure_resolved_document(
                tab_id,
                root_path=draft.root_path,
                title=draft.title,
                text=draft.current_text,
                line_sources=draft.line_sources,
                document_format=document_format,
            )
            if document is None:
                continue
            if getattr(view, "document", None) is not document:
                view.bind_document(document)
            supported = getattr(view, "set_editing_supported", None)
            if callable(supported):
                supported(True)
            set_editable = getattr(view, "set_editable", None)
            if callable(set_editable):
                set_editable(self.can_edit_documents())
            set_tab_busy = getattr(editor, "set_tab_busy", None)
            if callable(set_tab_busy):
                set_tab_busy(tab_id, False)
            view.set_content(
                document.current_text,
                document.line_sources,
                document.root_path,
            )
            view.set_dirty_state(document.is_dirty)
            editor.set_tab_dirty(tab_id, document.is_dirty)

    def autosave_project(self):
        project = self.project.active
        if project is None:
            return
        if self._autosave_running:
            # Coalescing: пока идёт запись, повторные запросы объединяются
            # в один отложенный проход для актуального проекта.
            self._autosave_pending = True
            self._autosave_pending_project = project
            return

        self._flush_editor_pending()
        self._autosave_running = True
        self._autosave_project = project
        self._autosave_pending = False
        self._autosave_pending_project = None

        def work(_log):
            return self.project.autosave(
                expected_project=project,
                should_stop=self._task_cancelled,
            )

        try:
            self.tasks.run(
                work,
                on_done=lambda saved, err: self._on_autosave_done(
                    project,
                    saved,
                    err,
                ),
                operation_key="project.autosave:%s" % project.id,
                title="Автосохранение: %s" % project.name,
                owner=project.id,
            )
        except Exception:
            self._autosave_running = False
            self._autosave_project = None
            raise

    def _on_autosave_done(self, project, saved, err):
        self._autosave_running = False
        self._autosave_project = None

        # События старого проекта не применяются и не публикуются:
        # подписчики не должны видеть чужой PROJECT_SAVED.
        if self.project.active is project:
            if err is not None:
                self.log("Проект: ошибка автосохранения: %s" % err)
            elif saved:
                self._apply_autosave(project, saved)

        if self._autosave_pending:
            pending = self._autosave_pending_project
            self._autosave_pending = False
            self._autosave_pending_project = None
            if pending is not None and self.project.active is pending:
                self.autosave_project()

    def _apply_autosave(self, project, saved):
        if self.project.active is not project:
            return
        events = [
            event for event in saved
            if self._autosave_event_exists(event)
        ]
        if not events:
            self._update_project_status()
            return
        self._invalidate_saved(events)

        if self.window is not None:
            for event in events:
                if event.kind == "file":
                    if not self._file_saved_current(event):
                        continue
                    # Текст в событии больше не передаётся: перед
                    # автосохранением вкладки уже синхронизированы.
                    self.window.editor.mark_clean(event.path)
                elif event.kind == "resolved":
                    if not self._resolved_saved_current(event):
                        continue
                    self.window.editor.mark_tab_clean(event.key)
                    document = self._resolved_documents.get(str(event.key))
                    if document is not None:
                        document.mark_saved()
                        view = self.window.editor.get_tab(document.tab_id)
                        if view is not None and hasattr(
                            view,
                            "set_dirty_state",
                        ):
                            if hasattr(view, "mark_clean"):
                                view.mark_clean(document.saved_text)
                            else:
                                view.set_dirty_state(False)

        for event in events:
            self.events.publish(Events.PROJECT_SAVED, event)

        self.set_status(
            "Автосохранение проекта: %d файл(ов)" % len(events),
            4000,
        )
        self.log("Проект: автосохранено файлов %d" % len(events))
        self._update_project_status()

    def _autosave_event_exists(self, event) -> bool:
        """Черновик события всё ещё принадлежит текущему проекту."""
        if event.kind == "file":
            return self.project.get_draft(event.path) is not None
        if event.kind == "asset":
            return self.project.get_asset_draft(event.path) is not None
        if event.kind == "resolved":
            return self.project.get_resolved_draft(event.key) is not None
        return False

    def _file_saved_current(self, event) -> bool:
        """Событие относится к актуальной версии черновика."""
        draft = self.project.get_draft(event.path)
        if draft is None:
            return False
        return (
            int(draft.revision) == int(event.revision)
            and not draft.is_dirty
        )

    def _resolved_saved_current(self, event) -> bool:
        draft = self.project.get_resolved_draft(event.key)
        if draft is None:
            return False
        return (
            int(draft.revision) == int(event.revision)
            and not draft.is_dirty
        )

    def on_app_close(self):
        self._closing = True
        if self.operations.is_active("vfs.rebuild"):
            self.tasks.cancel_operation("vfs.rebuild")
        if self.operations.is_active("project.create"):
            self.tasks.cancel_operation("project.create")
            self.set_status("Создание проекта: ожидание завершения…")
            return False
        if not self.project.is_active:
            # Активного проекта нет, но отложенные удаления могли
            # остаться от прошлого проекта. Закрытие — последняя точка,
            # где их можно разобрать без следующего load/create.
            try:
                self.project.flush()
            except Exception as exc:
                self.log(
                    "Проект: не удалось разобрать отложенные удаления "
                    "при закрытии: %s" % exc
                )
            return True
        active = self.project.active
        if self.operations.is_active(
            "project.delete:%s" % active.id
        ):
            self.set_status("Удаление проекта…")
            return False
        if getattr(self, "_patch_write_running", False):
            # Не блокируем UI и не разбираем окно: отменяем запись и
            # повторим закрытие из _on_patch_write_done.
            self._close_pending = True
            project = self.project.active
            if project is not None:
                try:
                    self.tasks.cancel_owner(project.id)
                except Exception:
                    pass
            self.set_status("Отмена записи патч-мода…")
            return False
        if getattr(self, "_close_flush_running", False):
            # Первое закрытие уже ждёт фонового сохранения.
            return False

        project = self.project.active
        self._flush_editor_pending()

        # Сохранение уже завершилось. Если пользователь ничего не менял
        # после него, окно можно закрывать без повторной синхронной
        # записи. Ошибку flush не повторяем: старый контракт закрытия
        # всё равно завершал приложение после одной попытки.
        if getattr(self, "_close_flush_project", None) is project:
            failed = bool(getattr(self, "_close_flush_failed", False))
            self._close_flush_project = None
            self._close_flush_failed = False
            if failed or not self.project.has_dirty():
                return True

        tabs = self._collect_open_tabs()
        if project is not None:
            try:
                self.tasks.cancel_owner(project.id)
            except Exception:
                pass
        self._autosave_pending = False
        self._autosave_pending_project = None
        # Старый фоновый проход не должен записать снимок после flush().
        self.project.invalidate_autosave()

        def work(_log):
            if self.project.active is not project:
                # Пока задача ждала, пользователь сменил проект: старый
                # уже сохранён через ProjectService.close().
                return []
            saved = self.project.flush()
            if tabs is not None and self.project.active is project:
                try:
                    self.project.save_tabs(tabs)
                except Exception as exc:
                    self.log(
                        "Проект: не удалось сохранить вкладки при "
                        "закрытии: %s" % exc
                    )
            return saved

        def done(_saved, error):
            self._close_flush_running = False
            self._close_flush_project = project
            self._close_flush_failed = error is not None
            if error is not None:
                self.log(
                    "Проект: ошибка сохранения при закрытии: %s" % error
                )
            close = getattr(self.window, "close", None)
            if callable(close):
                close()

        run = getattr(self.tasks, "run", None)
        if not callable(run):
            # Тестовый/встраиваемый контекст без TaskService: сохраняем
            # синхронно, иначе закроем приложение с потерей данных.
            self._save_open_tabs()
            try:
                self.project.flush()
            except Exception as exc:
                self.log(
                    "Проект: ошибка сохранения при закрытии: %s" % exc
                )
            return True

        self._close_flush_running = True
        try:
            run(
                work,
                on_done=done,
                owner=project.id,
                operation_key="project.close:%s" % project.id,
                title="Сохранение перед закрытием",
            )
        except Exception as exc:
            self._close_flush_running = False
            self.log(
                "Проект: не удалось запустить сохранение при закрытии: %s"
                % exc
            )
            self._save_open_tabs()
            try:
                self.project.flush()
            except Exception as flush_exc:
                self.log(
                    "Проект: ошибка сохранения при закрытии: %s"
                    % flush_exc
                )
            return True
        self.set_status("Сохранение проекта перед закрытием…")
        return False

    def _collect_open_tabs(self):
        """Собрать записи вкладок в UI-потоке для фоновой записи."""
        editor = self._editor_widget()
        collector = getattr(editor, "session_tabs", None)
        if not callable(collector):
            return None
        try:
            return collector()
        except Exception:
            logger.exception("Не удалось собрать список вкладок")
            return None

    def build_patch_preview(self):
        if self.window is None:
            return

        project = self.project.active
        if project is None:
            self.show_error("Создайте или загрузите проект.")
            return

        if self._forest is None:
            self.show_error(
                "Дерево не построено: сначала выполните «Пересобрать VFS»."
            )
            return

        if not self.project.has_changes():
            QMessageBox.information(
                self.window,
                "Проект",
                "В проекте нет изменённых файлов.",
            )
            return

        preview_key = "project.patch_preview:%s:%s" % (
            project.id,
            getattr(self._forest, "cache_id", "") or "",
        )
        if self.operations.is_active(preview_key):
            self.set_status("Предпросмотр патча уже выполняется", 5000)
            self.log("Проект: предпросмотр патча уже выполняется")
            return

        self.autosave_project()
        self.set_status("Подготовка патча проекта…")
        self.log("Проект: подготовка патча…")

        forest = self._forest

        def work(log):
            return self.project.build_preview(
                self.services.patch,
                forest,
                log=log,
                expected_project=project,
            )

        self.tasks.run(
            work,
            on_done=lambda preview, err: self._on_preview_ready(
                preview,
                err,
                project=project,
                forest=forest,
            ),
            on_log=self.log,
            operation_key=preview_key,
            title="Предпросмотр патча: %s" % project.name,
            owner=project.id,
        )

    def _on_preview_ready(self, preview, err, project=None, forest=None):
        if err is not None:
            self.set_status("Ошибка подготовки патча")
            self.log("Проект: ошибка подготовки патча: %s" % err)
            self.show_error(str(err))
            return

        if preview is None:
            return

        if (
            preview.project is not self.project.active
            or (project is not None and preview.project is not project)
        ):
            self.set_status("Проект изменён: предпросмотр устарел", 6000)
            self.log(
                "Проект: предпросмотр отменён — активный проект изменился"
            )
            return

        if forest is not None and not self._forest_is_current(forest):
            self.set_status("Лес устарел: предпросмотр отменён", 6000)
            self.log(
                "Проект: предпросмотр отменён — изменился снимок VFS"
            )
            return

        self.set_status(
            "Патч подготовлен: %d файл(ов)" % len(preview.items),
            5000,
        )

        self._patch_preview_ready = True
        self._apply_patch_action_label()
        self._verify_preview_roundtrip(preview, project, forest)

    def _verify_preview_roundtrip(self, preview, project, forest):
        """Полный round-trip в фоне; результат прикладывается к элементам."""
        verifier = self.services.patch.patch_roundtrip

        def work(log):
            reports = []
            for item in preview.items:
                report = None
                if item.is_patch and item.generated is not None:
                    if log:
                        log("Патч: полная проверка — %s" % item.title)
                    report = verifier.verify_report(item.generated)
                reports.append((item, report))
            return reports

        self.set_status("Полная проверка патча…")
        try:
            self.tasks.run(
                work,
                on_done=lambda reports, error: self._on_roundtrip_done(
                    preview,
                    reports,
                    error,
                    project,
                    forest,
                ),
                on_log=self.log,
                operation_key="project.roundtrip:%s:%s" % (
                    project.id,
                    getattr(forest, "cache_id", "") or "",
                ),
                title="Проверка патча: %s" % project.name,
                owner=project.id,
            )
        except Exception as exc:
            self.log("Патч: не удалось запустить полную проверку: %s" % exc)
            self._show_preview_dialog(preview)

    def _on_roundtrip_done(self, preview, reports, error, project, forest):
        if preview.project is not self.project.active:
            self.set_status("Проект изменён: проверка отменена", 6000)
            return
        if not self._forest_is_current(forest):
            self.set_status("Лес устарел: проверка отменена", 6000)
            return
        if error is not None:
            self.log("Патч: полная проверка не выполнена: %s" % error)
            self.show_error(
                "Полная проверка патча не выполнена:\n%s" % error
            )
            return

        reports = list(reports or ())
        items = tuple(
            replace(
                item,
                roundtrip=(
                    reports[index][1]
                    if index < len(reports)
                    else None
                ),
            )
            for index, item in enumerate(preview.items)
        )
        self._show_preview_dialog(replace(preview, items=items))

    def _show_preview_dialog(self, preview):
        dialog = ProjectPreviewDialog(
            preview,
            self.window,
            on_write=self.write_preview,
            on_rollback=self.rollback_preview_item,
            on_cancel=self.cancel_patch_write,
            write_enabled=patch_write_enabled(),
        )
        self._patch_preview_dialog = dialog
        try:
            dialog.exec_()
        finally:
            if getattr(self, "_patch_preview_dialog", None) is dialog:
                self._patch_preview_dialog = None

    def _forest_is_current(self, forest) -> bool:
        current = self._forest
        if current is None:
            return False
        current_id = getattr(current, "cache_id", "")
        if current_id:
            return current_id == getattr(forest, "cache_id", "")
        return forest is current

    def write_preview(self, preview):
        if not patch_write_enabled():
            self.show_error(
                "Запись патчей отключена: доступен только предпросмотр."
            )
            return False
        if getattr(self, "_patch_write_running", False):
            self.show_error(
                "Запись патч-мода уже выполняется: дождитесь завершения."
            )
            return False

        project = self.project.active
        if project is None:
            self.show_error("Создайте или загрузите проект.")
            return False
        forest = self._forest
        if forest is None:
            self.show_error(
                "Дерево не построено: сначала выполните «Пересобрать VFS»."
            )
            return False

        directory = QFileDialog.getExistingDirectory(
            self.window,
            "Выберите каталог, в котором будет создан патч-мод",
            "",
            QFileDialog.ShowDirsOnly,
        )
        if not directory:
            return False

        risks = []
        for item in preview.items:
            if not item.is_patch or item.excluded or not item.ok:
                continue
            if item.plan is None:
                continue
            risks.extend(self.services.patch.patch_commit.risk_keys(item.plan))
        risks = tuple(dict.fromkeys(risks))
        if risks:
            answer = QMessageBox.question(
                self.window,
                "Подтверждение патча",
                "Патч содержит опасные изменения:\n\n- %s"
                "\n\nЗаписать патч?" % "\n- ".join(risks),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return False

        asset_rules = self.asset_types.patch_rules()
        owner = project.id
        self._patch_write_running = True
        self.set_status("Запись патч-мода…")

        def cancelled():
            return self._task_cancelled()

        def work(log):
            return write_preview_use_case(
                self.project,
                self.services.patch,
                preview,
                directory,
                forest=forest,
                asset_rules=asset_rules,
                allow_write=True,
                confirmed_risks=risks,
                log=log,
                cancel_check=cancelled,
                snapshot_provider=lambda: self.workspace.snapshot_id,
            )

        try:
            self.tasks.run(
                work,
                on_done=self._on_patch_write_done,
                on_log=self.log,
                owner=owner,
                operation_key="project.patch_write:%s" % project.id,
                title="Запись патча: %s" % project.name,
            )
        except Exception as exc:
            self._patch_write_running = False
            self.show_error(
                "Не удалось запустить запись патч-мода: %s" % exc
            )
            return False
        return True

    def _patch_report_lines(self, result):
        lines = []
        for record in result.audit:
            report = record.roundtrip_report
            if report is None:
                continue
            for check in report.checks:
                if (
                    check.ok
                    and check.name not in (
                        "full_diff",
                        "source",
                        "dltx_artifacts",
                        "parents",
                        "roundtrip",
                    )
                ):
                    continue
                lines.append(
                    "%s: %s"
                    % (
                        check.name,
                        check.detail or ("OK" if check.ok else "ОШИБКА"),
                    )
                )
        return lines

    def cancel_patch_write(self):
        """Явно попросить фоновую запись завершиться."""
        project = self.project.active
        if project is None or not getattr(
            self,
            "_patch_write_running",
            False,
        ):
            return False
        try:
            self.tasks.cancel_owner(project.id)
        except Exception as exc:
            self.log("Проект: не удалось отменить запись: %s" % exc)
            return False
        self.set_status("Отмена записи патч-мода…")
        return True

    def _on_patch_write_done(self, result, error):
        self._patch_write_running = False
        dialog = getattr(self, "_patch_preview_dialog", None)
        if dialog is not None:
            try:
                dialog.finish_write(result, error)
            except RuntimeError:
                pass
        if getattr(self, "_close_pending", False):
            self._close_pending = False
            if dialog is not None:
                try:
                    dialog.accept()
                except RuntimeError:
                    pass
            if error is not None:
                self.log("Проект: ошибка записи при закрытии: %s" % error)
            if self.window is not None:
                self.window.close()
            return
        if error is not None:
            self.set_status("Ошибка записи патча", 6000)
            self.log("Проект: ошибка записи патча: %s" % error)
            self.show_error(str(error))
            return
        if result is None:
            return

        self.set_status(
            "Патч-мод создан: %s" % result.mod_dir.name,
            7000,
        )
        self.log("Проект: записано файлов патча %d" % len(result.written))
        for record in result.audit:
            self.log(
                "Патч: план %s, снимок %s, файлов %d"
                % (
                    record.plan_id,
                    record.snapshot_id,
                    len(record.paths),
                )
            )
        report_lines = self._patch_report_lines(result)
        for line in report_lines:
            self.log("Патч: " + line)

        if result.excluded_count:
            self.log(
                "Проект: исключено из патча файлов %d"
                % result.excluded_count
            )

        report_text = (
            "\n\nПолная проверка:\n%s" % "\n".join(report_lines)
            if report_lines
            else ""
        )
        if result.skipped:
            text = (
                "Записаны не все файлы. Пропущены:\n%s"
                % "\n".join(result.skipped)
            )
            if result.excluded_count:
                text += (
                    "\n\nИсключено из патча: %d."
                    % result.excluded_count
                )
            text += report_text
            QMessageBox.warning(self.window, "Проект", text)
        else:
            text = "Патч-мод создан:\n%s" % result.mod_dir
            if result.excluded_count:
                text += "\n\nИсключено из патча: %d." % result.excluded_count
            text += report_text
            QMessageBox.information(self.window, "Проект", text)

    def rollback_preview_item(self, item):
        """Удалить черновики файлов, из которых собран пункт патча."""
        try:
            result = project_use_cases.rollback_preview_item(
                self.project,
                item,
            )
        except ProjectError as exc:
            self.show_error(str(exc))
            return False

        try:
            self.refresh_workspace()
        except Exception as exc:
            self.log(
                "Проект: не удалось обновить VFS после отката: %s" % exc
            )
        try:
            self._invalidate_paths(result.invalidate_paths)
        except Exception as exc:
            self.log(
                "Проект: не удалось сбросить кэш после отката: %s" % exc
            )
        try:
            self._reload_open_documents()
        except Exception:
            logger.exception("Не удалось обновить документы после отката")

        self._update_project_status()
        name = Path(item.source_path).name or item.title
        self.log("Проект: изменения отменены: %s" % name)
        self.set_status("Изменения отменены: %s" % name, 5000)
        self._patch_preview_ready = True
        self._apply_patch_action_label()
        return True

    def _apply_patch_action_label(self):
        if self.window is None:
            return
        setter = getattr(self.window, "set_patch_rebuild_label", None)
        if callable(setter):
            setter(bool(getattr(self, "_patch_preview_ready", False)))

    def _update_project_status(self):
        if self.window is None:
            return
        setter = getattr(self.window, "set_project_status", None)
        if callable(setter):
            setter(self.project.status_text())

        # Держим доступность «Сформировать патч» в актуальном состоянии,
        # не дожидаясь повторного открытия меню.
        updater = getattr(self.window, "refresh_project_actions", None)
        if callable(updater):
            updater()

    # -------------------------------------------------- ui-сервисы
