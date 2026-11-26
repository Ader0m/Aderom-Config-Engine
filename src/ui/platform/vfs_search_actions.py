# -*- coding: utf-8 -*-
"""Платформенный сценарий поиска по содержимому собранного VFS.

Поиск выполняется в фоновой задаче и показывается в собственном окне.
Окно не читает файлы и не знает о ядре: сценарий передаёт ему готовые
совпадения и открывает выбранный файл через штатный маршрутизатор.
"""

from __future__ import annotations

import hashlib
import time

from sdk.ui import WindowSpec

from ..operations import CANCELLED
from ..tasks import current_task
from ..vfs_search import VfsSearchDialog


class VfsSearchAction:
    """Фоновый поиск строки по содержимому всех текстовых файлов VFS."""

    COMMAND = "platform.vfs_search"
    WINDOW_KEY = "platform.vfs_search.window"

    def __init__(self, api, services, tasks):
        self.api = api
        self.services = services
        self.tasks = tasks
        self._dialog = None
        self._request_seq = 0
        self._active_key = ""

    # -------------------------------------------------- окно
    def show(self):
        if self.api.workspace.forest is None:
            self.api.show_error(
                "Дерево не построено: дождитесь окончания сборки."
            )
            return False
        dialog = self._ensure_dialog()
        shown = self.api.open_window(
            WindowSpec(
                key=self.WINDOW_KEY,
                title="Поиск по VFS",
                kind="floating",
                state_key=self.WINDOW_KEY,
            ),
            dialog,
        )
        if shown:
            dialog.focus_query()
        return bool(shown)

    def _ensure_dialog(self):
        if self._dialog is None:
            self._dialog = VfsSearchDialog(
                self._search,
                self._open_match,
            )
        return self._dialog

    # -------------------------------------------------- поиск
    def _search(self, query, case_sensitive):
        forest = self.api.workspace.forest
        if forest is None:
            self.api.show_error(
                "Дерево не построено: дождитесь окончания сборки."
            )
            return

        overlay = self.api.project_overlay()
        source_files = overlay.source_files
        operation_key = self._operation_key(
            forest,
            overlay.revision_token,
            query,
            case_sensitive,
        )

        if self._active_key and self._active_key != operation_key:
            self.api.cancel_operation(self._active_key)
        already_running = self.api.operations.is_active(operation_key)

        self._request_seq += 1
        token = self._request_seq
        dialog = self._ensure_dialog()
        dialog.set_busy(query, already_running=already_running)
        self.api.set_status("Поиск по VFS: %s…" % query)
        self.api.log("Поиск по VFS: %s" % query)
        if already_running:
            self.api.log("Поиск по VFS уже выполняется.")

        started = time.monotonic()

        def work(log):
            return self.services.tree.search_text(
                forest,
                query,
                source_files=source_files,
                case_sensitive=case_sensitive,
                log=log,
                cancel_check=self._cancelled,
                progress=self._progress,
            )

        def done(result, err):
            if token != self._request_seq:
                return
            if self._active_key == operation_key:
                self._active_key = ""

            if err is not None:
                if self._was_cancelled(operation_key):
                    dialog.show_cancelled()
                    self.api.set_status("Поиск по VFS отменён", 5000)
                    return
                dialog.show_error(str(err))
                self.api.set_status("Ошибка поиска по VFS")
                self.api.log("Ошибка поиска по VFS: %s" % err)
                self.api.show_error(str(err))
                return

            elapsed = time.monotonic() - started
            dialog.show_results(result, elapsed)
            if result.matches:
                status = (
                    "Поиск по VFS: файлов %d, совпадений %d"
                    % (result.files_matched, result.total_matches)
                )
                self.api.log(
                    "Поиск по VFS '%s': файлов %d, совпадений %d"
                    % (
                        query,
                        result.files_matched,
                        result.total_matches,
                    )
                )
            else:
                status = "Поиск по VFS: совпадений не найдено"
                self.api.log(
                    "Поиск по VFS '%s': совпадений не найдено" % query
                )
            self.api.set_status(status, 5000)

        try:
            self._active_key = operation_key
            self.tasks.run(
                work,
                on_done=done,
                on_log=self.api.log,
                operation_key=operation_key,
                title="Поиск по VFS: %s" % _short_query(query),
            )
        except Exception as exc:
            if self._active_key == operation_key:
                self._active_key = ""
            dialog.show_error(str(exc))
            self.api.set_status("Ошибка поиска по VFS")
            self.api.log("Не удалось запустить поиск по VFS: %s" % exc)
            return

    @staticmethod
    def _operation_key(forest, overlay_revision, query, case_sensitive):
        digest = hashlib.sha1(
            str(query).encode("utf-8", "replace")
        ).hexdigest()[:16]
        snapshot = str(forest.cache_id or "")[:16]
        overlay = str(overlay_revision or "")[:16]
        return "vfs.search:%s:%s:%s:%s" % (
            "1" if case_sensitive else "0",
            digest,
            snapshot,
            overlay,
        )

    def _was_cancelled(self, operation_key):
        snapshot = self.api.operations.get(operation_key)
        return snapshot is not None and snapshot.state == CANCELLED

    @staticmethod
    def _cancelled():
        handle = current_task()
        return bool(handle is not None and handle.is_cancelled)

    @staticmethod
    def _progress(done, total, message):
        handle = current_task()
        if handle is not None:
            handle.progress(done=done, total=total, message=message)

    # -------------------------------------------------- открытие
    def _open_match(self, match, query):
        if not query:
            return
        self.api.log(
            "Поиск по VFS: открытие %s:%s"
            % (match.relative_path, match.first_line)
        )
        self.api.set_status(
            "Открытие: %s…" % match.relative_path,
            5000,
        )
        self.api.request_open_file_raw_with_search(match.path, query)


def _short_query(query):
    text = str(query or "").strip()
    if len(text) <= 60:
        return text
    return text[:59] + "…"


__all__ = ["VfsSearchAction"]
