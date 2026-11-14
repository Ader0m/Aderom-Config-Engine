# -*- coding: utf-8 -*-
"""Проектные операции PlatformApi.

Диалоги и файловые операции остаются в UI-слое, но отделены от
регистрации модулей и навигации.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import replace
from pathlib import Path

from PyQt5.QtWidgets import QApplication, QFileDialog, QMessageBox

from core import read_text_safe, read_xml_text
from sdk.events import Events
from sdk.api import AssetDraftInfo, SavedDraft

from app.projects import (
    ProjectAssetTypeError,
    ProjectError,
    ProjectSettingError,
)
from ..project_dialogs import (
    ProjectCreateDialog,
    ProjectLoadDialog,
    ProjectPreviewDialog,
    ProjectSettingsDialog,
)


logger = logging.getLogger(__name__)


class ProjectControllerMixin:
    """Жизненный цикл проекта, сохранение и предпросмотр патча."""

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
        return self.project.project_overlay()

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

    def _start_project_prebuild(self):
        """Пересобрать только затронутые проектом корни."""
        ensure = getattr(self.services, "ensure_prebuild", None)
        if not callable(ensure):
            return
        try:
            ensure(
                overlay=self.project_overlay(),
                roots=getattr(self, "_project_prebuild_roots", None),
            )
        except Exception as exc:
            self.log(
                "Проект: не удалось запустить пересборку: %s" % exc
            )

    def workspace_forest(self):
        return self.workspace.forest

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
            services=self.services,
        )
        if added or removed:
            self.events.publish(Events.TREE_READY, forest)
            try:
                roots = self.workspace.roots_for(
                    self.workspace.last_added_paths
                ) | set(self.workspace.last_removed_roots)
                invalidate = getattr(
                    self.services,
                    "invalidate_paths",
                    None,
                )
                if roots and callable(invalidate):
                    invalidate(roots)
                else:
                    self._invalidate_resolver_cache()
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
            run(work, on_done=finish)
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

    def register_project_asset_type(self, asset_type):
        """Зарегистрировать тип двоичного ресурса от имени модуля."""
        try:
            registered = self.asset_types.register(asset_type)
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

    def unregister_project_asset_type(self, key):
        if not self.asset_types.unregister(key):
            return False
        self.project.invalidate_preview_cache()
        self.log("Проект: тип ресурса снят: %s" % key)
        return True

    def project_asset_types(self):
        return self.asset_types.types()

    # -------------------------------------------------- настройки модулей
    def register_project_setting(self, setting):
        """Зарегистрировать настройку проекта от имени модуля."""
        try:
            registered = self.project_setting_registry.register(setting)
        except ProjectSettingError as exc:
            self.log("Проект: настройка не зарегистрирована: %s" % exc)
            return False

        self.project.invalidate_preview_cache()
        self.log(
            "Проект: зарегистрирована настройка %s (%s)"
            % (registered.key, registered.label)
        )
        return True

    def unregister_project_setting(self, key):
        if not self.project_setting_registry.unregister(key):
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
            text="",
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

    def set_editor_tab_dirty(self, tab_id, dirty):
        if self.window is None:
            return
        editor = getattr(self.window, "editor", None)
        setter = getattr(editor, "set_tab_dirty", None)
        if callable(setter):
            setter(tab_id, bool(dirty))

    def set_editor_tab_busy(self, tab_id, busy):
        if self.window is None:
            return
        editor = getattr(self.window, "editor", None)
        setter = getattr(editor, "set_tab_busy", None)
        if callable(setter):
            setter(str(tab_id or ""), bool(busy))

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
                reader = getattr(self.services, "read_xml_text", None)
            else:
                reader = getattr(self.services, "read_text", None)
            if callable(reader):
                return reader(path)
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
        except Exception:
            logger.exception("Не удалось обновить черновик %s", path)
            self.log("Проект: непредвиденная ошибка черновика (crash.log)")
            return

        # Черновик входит в VFS: ранее собранные корни больше нельзя
        # считать актуальными, даже если файл на диске не менялся.
        try:
            self._invalidate_paths([path])
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
            self._invalidate_paths([view.root_path])
        except Exception as exc:
            self.log("Проект: не удалось сбросить кэш резолва: %s" % exc)

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
        self._set_project_loading(True)

    def _end_project_activation(self):
        self._set_project_loading(False)

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
        self.events.publish(Events.PROJECT_CHANGED, project)
        try:
            self._reload_open_documents()
        except Exception:
            logger.exception("Не удалось обновить открытые документы")
            self.log(
                "Проект: не удалось обновить открытые документы "
                "(crash.log)"
            )
        self._start_project_prebuild()
        self.log(message)
        self.set_status(message, 5000)

    def create_project(self):
        if self.window is None:
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

        self._begin_project_activation()
        try:
            try:
                project = self.project.create(
                    name,
                    [],
                    dialog.save_mode(),
                    xml_patch_mode=dialog.xml_patch_mode(),
                    project_dir=project_dir,
                )
            except ProjectError as exc:
                self.show_error(str(exc))
                return

            for key, value in dialog.module_setting_values().items():
                self.set_project_setting(key, value)

            self._activate_project_state(
                project,
                "Проект создан: %s" % project.name,
            )
        finally:
            self._end_project_activation()

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

        self._begin_project_activation()
        try:
            try:
                project = self.project.load(project_id)
            except ProjectError as exc:
                self.show_error(str(exc))
                return

            self._activate_project_state(
                project,
                "Проект загружен: %s" % project.name,
            )
        finally:
            self._end_project_activation()

    def _delete_project(self):
        self._begin_project_activation()
        try:
            try:
                self.project.delete_active_project()
            except ProjectError as exc:
                self.show_error(str(exc))
                return False

            self._restore_project_virtual_files()
            self._invalidate_resolver_cache()
            self._update_project_status()
            self.events.publish(Events.PROJECT_CHANGED, None)
            self.log("Проект удалён")
            self.set_status("Проект удалён", 5000)
            return True
        finally:
            self._end_project_activation()

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
            document.reset(
                draft.current_text,
                line_sources=draft.line_sources,
                saved_text=draft.saved_text,
                original_text=draft.original_text,
                persist=False,
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
        if not self.project.is_active:
            return

        self._flush_editor_pending()
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
            "Автосохранение проекта: %d файл(ов)" % len(saved),
            4000,
        )
        self.log("Проект: автосохранено файлов %d" % len(saved))
        self._update_project_status()

    def on_app_close(self):
        if not self.project.is_active:
            return
        self._flush_editor_pending()
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
                "Дерево не построено: сначала выполните «Пересобрать VFS»."
            )
            return

        if (
            not self.project.changed_drafts()
            and not self.project.changed_resolved_drafts()
            and not self.project.changed_asset_drafts()
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

        if preview.project is not self.project.active:
            self.set_status("Проект изменён: предпросмотр устарел", 6000)
            self.log(
                "Проект: предпросмотр отменён — активный проект изменился"
            )
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
        if preview.project is not self.project.active:
            self.show_error(
                "Активный проект изменился: патч не записан."
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
                    asset_rules=self.asset_types.patch_rules(),
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
        updater = getattr(self.window, "refresh_project_actions", None)
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
            from ..theme import apply_override_theme, apply_window_caption

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
