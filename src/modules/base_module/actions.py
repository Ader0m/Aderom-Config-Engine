# -*- coding: utf-8 -*-
"""Действия модуля над деревом и редактором."""

from pathlib import Path


class TreeActions:
    """Контекстные действия дерева файлов."""

    def __init__(self, ctx, session):
        self.ctx = ctx
        self.session = session

    def open_raw(self, path):
        self.ctx.ui.open_raw(path)

    def open_in_external_editor(self, path):
        ok, message = self.ctx.services.open_external_editor(path)
        if not ok:
            self.ctx.ui.show_error(message)

    def assemble_and_open(self, path):
        if not self.session.has_tree:
            self.ctx.ui.show_error(
                "Нет дерева: сначала выполните «Чистый запуск»."
            )
            return

        path = Path(path)
        self.ctx.ui.set_status("Сборка: %s…" % path.name)

        forest = self.session.forest

        def work(log):
            out = self.ctx.services.assemble_to_file(path, forest)
            ok, message = self.ctx.services.open_external_editor(out)
            if not ok:
                raise RuntimeError(message)
            return out

        self.ctx.tasks.run(
            work,
            on_done=self._on_assemble_done,
            on_log=self.ctx.ui.log,
        )

    def _on_assemble_done(self, out, err):
        if err is not None:
            self.ctx.ui.set_status("Ошибка")
            self.ctx.ui.show_error(str(err))
            return

        self.ctx.ui.set_status("Открыт: %s" % Path(out).name, 5000)

    def copy_relative_path(self, path):
        if self.session.has_tree:
            text = self.ctx.services.relative_path(
                self.session.forest,
                Path(path),
            )
        else:
            text = str(path)

        self.ctx.ui.copy_to_clipboard(text)

    def sort_tree_by_files_desc(self, path=None):
        self.ctx.ui.sort_file_tree_by_files_desc()


class EditorActions:
    """Стандартные действия редактора, которые модуль добавляет в меню."""

    def __init__(self, ctx):
        self.ctx = ctx

    def undo(self):
        self.ctx.ui.undo()

    def redo(self):
        self.ctx.ui.redo()

    def find(self):
        self.ctx.ui.find_in_editor()
