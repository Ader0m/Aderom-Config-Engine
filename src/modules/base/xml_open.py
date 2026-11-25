# -*- coding: utf-8 -*-
"""Открытие XML-файлов: сырое и после XmlResolver (include + DXML)."""

from pathlib import Path

from sdk import FileOpenHandler


def xml_resolved_tab_id(relative_path) -> str:
    """Единый id resolved-вкладки XML базового модуля."""
    return "base.resolved_xml:%s" % relative_path


class XmlFileOpener:
    """Открывает .xml как сырой текст или как результат XmlResolver."""

    def __init__(self, ctx, session=None):
        self.ctx = ctx
        self.session = session

    def _current_request_id(self):
        """id запроса открытия для асинхронного продолжения."""
        getter = getattr(self.ctx.ui, "current_file_request_id", None)
        if not callable(getter):
            return 0
        try:
            return int(getter() or 0)
        except Exception:
            return 0

    def _open_failed(self, path, reason="", request_id=0):
        self.ctx.ui.notify_file_open_failed(
            path,
            reason=reason,
            request_id=request_id or None,
        )

    def _focus_open_tab(self, tab_id, line_number=None) -> bool:
        """Активировать уже открытую resolved-вкладку без повторного резолва."""
        ui = self.ctx.ui
        request_tab = getattr(ui, "request_tab", None)
        if not callable(request_tab):
            return False
        try:
            if not request_tab(tab_id):
                return False
        except Exception as exc:
            ui.log("Не удалось переключиться на вкладку %s: %s" % (tab_id, exc))
            return False

        opener = getattr(ui, "open_resolved_document", None)
        if not callable(opener):
            return True
        try:
            activated = bool(opener(tab_id, line_number))
        except Exception as exc:
            ui.log("Не удалось активировать вкладку %s: %s" % (tab_id, exc))
            return False
        return activated

    def _activate_existing(self, path, line_number=None) -> bool:
        """Переключиться на уже открытую XML-вкладку, если она есть."""
        rel = self._relative(path)
        tab_id = xml_resolved_tab_id(rel)
        if not self._focus_open_tab(tab_id, line_number):
            return False
        self.ctx.ui.set_status("Файл уже открыт: %s" % rel, 3000)
        self.ctx.ui.log("Файл уже открыт: %s" % rel)
        return True

    # -------------------------------------------------- сырой текст
    def open_default(self, path) -> bool:
        """Открытие по умолчанию: резолв, если дерево/конфиги доступны."""
        if (
            self.session is not None
            and getattr(self.session, "has_tree", False)
            and getattr(self.session, "config_dir", None) is not None
        ):
            if self.open_resolved(path):
                return True
        return self.open_raw(path)

    def open_raw(self, path) -> bool:
        path = Path(path)

        self.ctx.ui.set_status("XML (без резолва): %s…" % path.name)
        self.ctx.ui.log("XML (без резолва): %s" % path.name)
        self.ctx.ui.open_raw(path)
        self.ctx.ui.set_status("Открыт XML: %s" % path.name, 5000)
        return True

    # -------------------------------------------------- XmlResolver
    def open_resolved_at_line(self, path, line_number) -> bool:
        """Открыть XML после резолва и перейти к строке (для графа)."""
        return self.open_resolved(path, line_number=line_number)

    def open_resolved(self, path, line_number=None) -> bool:
        path = Path(path)
        request_id = self._current_request_id()

        if not self._check_session(path, request_id):
            return False

        # Уже открытая вкладка не требует повторного XML-резолва.
        if self._activate_existing(path, line_number):
            return True

        self.ctx.ui.set_status("XML-резолв: %s…" % path.name)
        self.ctx.ui.log("XML-резолв: %s" % path.name)

        config_dir = self.session.config_dir
        overlay_fn = getattr(self.ctx.ui, "project_overlay", None)
        overlay = overlay_fn() if callable(overlay_fn) else None

        def work(log):
            return self.ctx.services.resolve_xml(
                path,
                config_dir,
                log=log,
                overlay=overlay,
            )

        try:
            self.ctx.tasks.run(
                work,
                on_done=lambda result, err: self._on_resolved(
                    path,
                    result,
                    err,
                    line_number,
                    request_id,
                ),
                on_log=self.ctx.ui.log,
            )
        except Exception as exc:
            self._open_failed(
                path,
                "Не удалось запустить XML-резолв",
                request_id,
            )
            self.ctx.ui.show_error(
                "Не удалось запустить XML-резолв %s:\n%s" % (path.name, exc)
            )
            return False
        return True

    def open_resolved_in_external_editor(self, path) -> bool:
        path = Path(path)
        request_id = self._current_request_id()

        if not self._check_session(path, request_id):
            return False

        self.ctx.ui.set_status("XML-резолв: %s…" % path.name)
        self.ctx.ui.log("XML-резолв: %s" % path.name)
        config_dir = self.session.config_dir
        overlay_fn = getattr(self.ctx.ui, "project_overlay", None)
        overlay = overlay_fn() if callable(overlay_fn) else None

        def work(log):
            out = self.ctx.services.resolve_xml_to_file(
                path,
                config_dir,
                log=log,
                overlay=overlay,
            )
            ok, message = self.ctx.services.open_external_editor(out)
            if not ok:
                raise RuntimeError(message)
            return out

        try:
            self.ctx.tasks.run(
                work,
                on_done=lambda out, err: self._on_resolved_external(
                    path,
                    out,
                    err,
                    request_id,
                ),
                on_log=self.ctx.ui.log,
            )
        except Exception as exc:
            self._open_failed(
                path,
                "Не удалось запустить XML-резолв",
                request_id,
            )
            self.ctx.ui.show_error(
                "Не удалось запустить XML-резолв %s:\n%s" % (path.name, exc)
            )
            return False
        return True

    # -------------------------------------------------- служебное
    def _check_session(self, path, request_id=0):
        if self.session is None or not getattr(self.session, "has_tree", False):
            self._open_failed(
                path,
                "Дерево не построено",
                request_id,
            )
            self.ctx.ui.show_error(
                "Дерево не построено: дождитесь окончания сборки."
            )
            return False

        if getattr(self.session, "config_dir", None) is None:
            self._open_failed(
                path,
                "Не определён каталог configs",
                request_id,
            )
            self.ctx.ui.show_error(
                "Не определён каталог configs — XML-резолв невозможен."
            )
            return False
        return True

    def _relative(self, path) -> str:
        if self.session is not None and getattr(self.session, "forest", None):
            try:
                return self.ctx.services.relative_path(
                    self.session.forest,
                    path,
                )
            except Exception:
                pass
        return Path(path).name

    def _on_resolved(self, path, result, err, line_number=None,
                     request_id=0):
        if err is not None:
            self._open_failed(
                path,
                "Ошибка XML-резолва",
                request_id,
            )
            self.ctx.ui.set_status("Ошибка")
            self.ctx.ui.log("Ошибка XML-резолва %s: %s" % (Path(path).name, err))
            self.ctx.ui.show_error(
                "Не удалось разрешить XML %s:\n%s" % (Path(path).name, err)
            )
            return

        for note in getattr(result, "notes", ()) or ():
            self.ctx.ui.log("[xml-resolved] %s" % note)

        rel = self._relative(path)
        tab_id = xml_resolved_tab_id(rel)
        mode = " + DXML" if getattr(result, "dxml_applied", False) else ""
        self.ctx.documents.open_xml_resolved_tab(
            tab_id,
            "RESOLVED XML%s: %s" % (mode, rel),
            result.text,
            str(path),
            line_sources=getattr(result, "line_sources", ()) or (),
            line_number=line_number,
            request_id=request_id or None,
        )
        self.ctx.ui.set_status(
            "Открыт XML-резолв%s: %s" % (mode, rel),
            5000,
        )
        self.ctx.ui.log("XML-резолв готов%s: %s" % (mode, rel))

    def _on_resolved_external(self, path, out, err, request_id=0):
        if err is not None:
            self._open_failed(
                path,
                "Ошибка XML-резолва",
                request_id,
            )
            self.ctx.ui.set_status("Ошибка")
            self.ctx.ui.log("Ошибка XML-резолва: %s" % err)
            self.ctx.ui.show_error(str(err))
            return
        self.ctx.ui.set_status("Открыт: %s" % Path(out).name, 5000)
        self.ctx.ui.log("XML-резолв открыт в редакторе: %s" % Path(out).name)


class XmlOpenHandler(FileOpenHandler):
    """Перехватывает открытие .xml: по умолчанию — XmlResolver."""

    priority = 200

    def __init__(self, opener: XmlFileOpener):
        self._opener = opener

    def can_open(self, path):
        return Path(path).suffix.lower() == ".xml"

    def open(self, path, api):
        return self._opener.open_default(path)
