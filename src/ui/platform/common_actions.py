# -*- coding: utf-8 -*-
"""Общие действия дерева и редактора, не зависящие от LTX/XML.

Принадлежат платформе; модули больше не регистрируют эти команды и меню
(ADR-33).
"""

from __future__ import annotations

from pathlib import Path


class CommonActions:
    def __init__(self, api, services, tasks):
        self.api = api
        self.services = services
        self.tasks = tasks

    def open_raw(self, path):
        self.api.request_open_file_raw(path)

    def open_in_external_editor(self, path):
        self.api.log("Открытие в Notepad++: %s" % Path(path).name)
        ok, message = self.services.resolve.open_external_editor(path)
        if not ok:
            self.api.log("Ошибка Notepad++: %s" % message)
            self.api.show_error(message)

    def assemble_and_open(self, path):
        forest = self.api.workspace.forest
        if forest is None:
            self.api.show_error(
                "Нет дерева: сначала выполните «Пересобрать VFS»."
            )
            return

        path = Path(path)
        self.api.set_status("Сборка: %s…" % path.name)
        self.api.log("Сборка: %s" % path.name)

        def work(log):
            out = self.services.resolve.assemble_to_file(path, forest)
            ok, message = self.services.resolve.open_external_editor(out)
            if not ok:
                raise RuntimeError(message)
            return out

        self.tasks.run(
            work,
            on_done=self._on_assemble_done,
            on_log=self.api.log,
            operation_key="platform.assemble:%s" % self.api.paths.key(path),
            title="Сборка: %s" % path.name,
        )

    def _on_assemble_done(self, out, err):
        if err is not None:
            self.api.set_status("Ошибка")
            self.api.log("Ошибка сборки: %s" % err)
            self.api.show_error(str(err))
            return
        self.api.set_status("Открыт: %s" % Path(out).name, 5000)
        self.api.log("Сборка открыта: %s" % Path(out).name)

    def copy_relative_path(self, path):
        forest = self.api.workspace.forest
        if forest is not None:
            text = self.services.tree.relative_path(forest, Path(path))
        else:
            text = str(path)
        self.api.clipboard_set(text)

    def sort_tree_by_files_desc(self, path=None):
        self.api.sort_file_tree_by_files_desc()

    def undo(self):
        self.api.commands.execute("ui.editor_undo")

    def redo(self):
        self.api.commands.execute("ui.editor_redo")

    def find(self):
        self.api.commands.execute("ui.editor_find")
