# -*- coding: utf-8 -*-
"""Действия модуля над деревом и редактором."""

from pathlib import Path


def is_xml_path(path) -> bool:
    """True для файлов с расширением .xml."""
    return Path(path).suffix.lower() == ".xml"


def is_ltx_path(path) -> bool:
    """True для путей, которые открываются LTX-путём (не XML)."""
    return Path(path).suffix.lower() != ".xml"


class TreeActions:
    """Контекстные действия дерева файлов."""

    def __init__(self, ctx, session, raw_opener=None):
        self.ctx = ctx
        self.session = session
        self._raw_opener = raw_opener

    def open_raw(self, path):
        self.ctx.ui.log("Открытие исходного файла: %s" % Path(path).name)
        if self._raw_opener is not None:
            self._raw_opener(path)
        else:
            self.ctx.ui.open_raw(path)

    def open_in_external_editor(self, path):
        self.ctx.ui.log("Открытие в Notepad++: %s" % Path(path).name)
        ok, message = self.ctx.services.open_external_editor(path)
        if not ok:
            self.ctx.ui.log("Ошибка Notepad++: %s" % message)
            self.ctx.ui.show_error(message)

    def assemble_and_open(self, path):
        if not self.session.has_tree:
            self.ctx.ui.show_error(
                "Нет дерева: сначала выполните «Пересобрать VFS»."
            )
            return

        path = Path(path)
        self.ctx.ui.set_status("Сборка: %s…" % path.name)
        self.ctx.ui.log("Сборка: %s" % path.name)

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
            self.ctx.ui.log("Ошибка сборки: %s" % err)
            self.ctx.ui.show_error(str(err))
            return

        self.ctx.ui.set_status("Открыт: %s" % Path(out).name, 5000)
        self.ctx.ui.log("Сборка открыта: %s" % Path(out).name)

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
