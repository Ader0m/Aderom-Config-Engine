# -*- coding: utf-8 -*-
"""Открытие XML-файлов: сырое и после XmlResolver (include + DXML)."""

from pathlib import Path

from sdk import FileOpenFailed, FileOpenHandler
from sdk.events import Events


class XmlFileOpener:
    """Открывает .xml как сырой текст или как результат XmlResolver."""

    def __init__(self, ctx, session=None):
        self.ctx = ctx
        self.session = session

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

        if not self._check_session(path):
            return False

        self.ctx.ui.set_status("XML-резолв: %s…" % path.name)
        self.ctx.ui.log("XML-резолв: %s" % path.name)

        config_dir = self.session.config_dir

        def work(log):
            return self.ctx.services.resolve_xml(
                path,
                config_dir,
                log=log,
            )

        try:
            self.ctx.tasks.run(
                work,
                on_done=lambda result, err: self._on_resolved(
                    path,
                    result,
                    err,
                    line_number,
                ),
                on_log=self.ctx.ui.log,
            )
        except Exception as exc:
            self.ctx.ui.show_error(
                "Не удалось запустить XML-резолв %s:\n%s" % (path.name, exc)
            )
            return False
        return True

    def open_resolved_in_external_editor(self, path) -> bool:
        path = Path(path)

        if not self._check_session(path):
            return False

        self.ctx.ui.set_status("XML-резолв: %s…" % path.name)
        self.ctx.ui.log("XML-резолв: %s" % path.name)
        config_dir = self.session.config_dir

        def work(log):
            out = self.ctx.services.resolve_xml_to_file(
                path,
                config_dir,
                log=log,
            )
            ok, message = self.ctx.services.open_external_editor(out)
            if not ok:
                raise RuntimeError(message)
            return out

        try:
            self.ctx.tasks.run(
                work,
                on_done=self._on_resolved_external,
                on_log=self.ctx.ui.log,
            )
        except Exception as exc:
            self.ctx.ui.show_error(
                "Не удалось запустить XML-резолв %s:\n%s" % (path.name, exc)
            )
            return False
        return True

    # -------------------------------------------------- служебное
    def _check_session(self, path):
        if self.session is None or not getattr(self.session, "has_tree", False):
            self.ctx.events.publish(
                Events.FILE_OPEN_FAILED,
                FileOpenFailed(path=Path(path)),
            )
            self.ctx.ui.show_error(
                "Дерево не построено: сначала «Пересобрать VFS»."
            )
            return False

        if getattr(self.session, "config_dir", None) is None:
            self.ctx.events.publish(
                Events.FILE_OPEN_FAILED,
                FileOpenFailed(path=Path(path)),
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

    def _on_resolved(self, path, result, err, line_number=None):
        if err is not None:
            self.ctx.ui.set_status("Ошибка")
            self.ctx.ui.log("Ошибка XML-резолва %s: %s" % (Path(path).name, err))
            self.ctx.ui.show_error(
                "Не удалось разрешить XML %s:\n%s" % (Path(path).name, err)
            )
            return

        for note in getattr(result, "notes", ()) or ():
            self.ctx.ui.log("[xml-resolved] %s" % note)

        rel = self._relative(path)
        tab_id = "base.resolved_xml:%s" % rel
        mode = " + DXML" if getattr(result, "dxml_applied", False) else ""
        self.ctx.ui.open_xml_resolved_tab(
            tab_id,
            "RESOLVED XML%s: %s" % (mode, rel),
            result.text,
            str(path),
            line_sources=getattr(result, "line_sources", ()) or (),
            line_number=line_number,
        )
        self.ctx.ui.set_status(
            "Открыт XML-резолв%s: %s" % (mode, rel),
            5000,
        )
        self.ctx.ui.log("XML-резолв готов%s: %s" % (mode, rel))

    def _on_resolved_external(self, out, err):
        if err is not None:
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
