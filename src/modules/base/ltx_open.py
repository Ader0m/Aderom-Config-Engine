# -*- coding: utf-8 -*-
"""Открытие LTX-файлов: резолв базовых файлов и просмотр DLTX-патчей."""

from pathlib import Path

from sdk import FileOpenHandler


def resolved_tab_id(relative_path) -> str:
    """Единый id resolved-вкладки базового модуля."""
    return "base.resolved:%s" % relative_path


def raw_tab_id(relative_path) -> str:
    """Единый id сырой вкладки базового модуля."""
    return "base.raw:%s" % relative_path


class LtxFileOpener:
    """Показывает .ltx так, как его видит игра, либо как исходный патч."""

    def __init__(self, ctx, session):
        self.ctx = ctx
        self.session = session

    def _project_overlay(self):
        fn = getattr(self.ctx.ui, "project_overlay", None)
        if not callable(fn):
            return None
        try:
            return fn()
        except Exception as exc:
            self.ctx.ui.log(
                "Слой правок проекта недоступен: %s" % exc
            )
            return None

    def open(self, path) -> bool:
        path = Path(path)
        if not self.session.has_tree:
            self._open_failed(path)
            self.ctx.ui.show_error(
                "Дерево не построено: дождитесь окончания сборки."
            )
            return False

        if path.name.lower().startswith("mod_"):
            return self.open_raw(path)

        return self._open_resolved(path)

    def _current_request_id(self):
        """id запроса открытия для асинхронного продолжения."""
        getter = getattr(self.ctx.ui, "current_file_request_id", None)
        if not callable(getter):
            return 0
        try:
            return int(getter() or 0)
        except Exception:
            return 0

    def _open_failed(self, path, request_id=0):
        """Сообщить платформе, что открытие этого пути не состоится."""
        self.ctx.ui.notify_file_open_failed(
            path,
            request_id=request_id or None,
        )

    def _relative(self, path) -> str:
        """Относительный путь файла в текущем лесу (имя как запасной)."""
        forest = self.session.forest
        if forest is None:
            return Path(path).name
        try:
            return self.ctx.services.relative_path(forest, path)
        except Exception:
            return Path(path).name

    def _focus_open_tab(self, tab_id, line_number=None) -> bool:
        """Активировать уже открытую resolved-вкладку без пересборки.

        ``request_tab`` возвращает ``True`` только если вкладка существует;
        ``open_resolved_document`` публикует ``FILE_OPENED`` с текущим
        ``request_id`` (нужен для отложенного перехода) и переходит к
        строке. Повторный резолв и перерисовка не выполняются.
        """
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
            # Вкладка уже активна; без публикации события её можно
            # считать открытой, но отложенная навигация не сработает.
            return True
        try:
            activated = bool(opener(tab_id, line_number))
        except Exception as exc:
            ui.log("Не удалось активировать вкладку %s: %s" % (tab_id, exc))
            return False
        if not activated:
            # Вкладка осиротела (нет живого документа и черновика):
            # обычный резолв восстановит её содержимое.
            return False
        return True

    def _activate_existing(self, path, line_number=None) -> bool:
        """Переключиться на уже открытую вкладку файла, если она есть."""
        rel = self._relative(path)
        tab_id = resolved_tab_id(rel)
        if not self._focus_open_tab(tab_id, line_number):
            return False
        self.ctx.ui.set_status("Файл уже открыт: %s" % rel, 3000)
        self.ctx.ui.log("Файл уже открыт: %s" % rel)
        return True

    # -------------------------------------------------- сырой просмотр
    def open_raw(self, path, line_number=None) -> bool:
        """Единственная точка входа для сырого просмотра.

        Для ``mod_*`` добавляется шапка DLTX-патча, для остальных —
        шапка сырого файла. Патч можно править в проекте: он всегда
        сохраняется полной заменой файла. Обычный сырой файл остаётся
        доступен только для чтения.
        """
        path = Path(path)
        if not self.session.has_tree:
            self._open_failed(path)
            self.ctx.ui.show_error(
                "Дерево не построено: дождитесь окончания сборки."
            )
            return False

        is_patch = path.name.lower().startswith("mod_")
        label = "Патч" if is_patch else "Сырой файл"
        self.ctx.ui.set_status("%s: %s…" % (label, path.name))
        self.ctx.ui.log("%s: %s" % (label, path.name))

        raw_content = self._read_raw(path)
        if raw_content is None:
            return False

        forest = self.session.forest
        rel = self.ctx.services.relative_path(forest, path)
        header, title = self._raw_header(
            path,
            rel,
            is_patch,
        )
        self.ctx.ui.show_text(
            raw_content,
            title,
            readonly=not is_patch,
            source_path=path,
            line_number=(
                int(line_number) if line_number is not None else None
            ),
            syntax=True,
            notice="\n".join(header).strip(),
            tab_id=raw_tab_id(rel),
        )
        self.ctx.ui.set_status("Открыт %s: %s" % (label.lower(), rel), 5000)
        return True

    def _raw_header(self, path: Path, rel: str, is_patch: bool):
        """Шапка сырого просмотра. Одна для патчей и обычных файлов."""
        if not is_patch:
            return [
                "; ═══════════════════════════════════════════════════════════",
                ";                СЫРОЙ ФАЙЛ (БЕЗ РЕЗОЛВА)",
                "; ═══════════════════════════════════════════════════════════",
                "; Файл: %s" % rel,
                "; Это исходный файл без include, DLTX и итоговых значений.",
                "; Итоговые значения: откройте %s обычным открытием." % rel,
                "; ═══════════════════════════════════════════════════════════",
                "",
            ], "[Raw]%s" % rel

        forest = self.session.forest
        root = self.ctx.services.patch_target(forest, path)
        header = [
            "; ═══════════════════════════════════════════════════════════",
            ";                    DLTX-ПАТЧ",
            "; ═══════════════════════════════════════════════════════════",
            "; Файл: %s" % rel,
            "; Тип: Патч (не загружается отдельно движком)",
            "; Правка сохраняется полной заменой файла.",
        ]
        if root is not None:
            header += [
                "; Применяется к: %s"
                % self.ctx.services.relative_path(forest, root),
                "; ",
                "; Чтобы увидеть итоговое состояние с применённым патчем,",
                ";   откройте корневой файл: %s" % Path(root).name,
            ]
        else:
            header += [
                "; ",
                "; ⚠️ Патч не подключился ни к одному базовому файлу!",
                "; Проверьте имя (должно быть mod_<имя_базы>_<что-то>.ltx)",
            ]
        header += [
            "; ═══════════════════════════════════════════════════════════",
            "",
        ]
        title = "DLTX PATCH: %s" % rel
        if root is None:
            title = "⚠️ " + title
        return header, title

    def _read_raw(self, path: Path):
        try:
            # Виртуальный черновик проекта может ещё не существовать
            # на диске, поэтому сначала спрашиваем текст у платформы.
            raw_content = self.ctx.documents.read_project_text(path)
            if not raw_content and Path(path).is_file():
                raw_content = self.ctx.services.read_text(path)
            return raw_content or ""
        except Exception as exc:
            self._open_failed(path)
            self.ctx.ui.show_error("Не удалось прочитать: %s" % exc)
            return None

    # -------------------------------------------------- резолв
    def open_resolved_at_line(self, path, line_number) -> bool:
        """Принудительно открыть резолв файла и перейти к строке.

        Нужно графу связей: он ищет по итоговым файлам резолвера,
        в том числе для mod_*-файлов.
        """
        return self._open_resolved(path, line_number=line_number)

    def _open_resolved(self, path: Path, line_number=None) -> bool:
        # Уже открытая вкладка не требует повторного резолва: достаточно
        # сделать её активной (и перейти к строке, если он задан).
        if self._activate_existing(path, line_number):
            return True

        self.ctx.ui.set_status("Резолв: %s…" % path.name)
        self.ctx.ui.log("Резолв файла: %s" % path.name)

        request_id = self._current_request_id()
        forest = self.session.forest
        config_dir = self.session.config_dir
        overlay = self._project_overlay()

        def work(log):
            # ВАЖНО: именно assemble_resolved, а не assemble_text —
            # он возвращает кортеж (text, line_sources)
            return self.ctx.services.assemble_resolved(
                path,
                forest,
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
                operation_key="base.ltx.resolve:%s" % self.ctx.paths.key(path),
                title="LTX-резолв: %s" % path.name,
            )
        except Exception as exc:
            self._open_failed(path, request_id)
            self.ctx.ui.show_error(
                "Не удалось запустить сборку %s:\n%s" % (path.name, exc)
            )
            return False
        return True

    def _on_resolved(self, path: Path, result, err, line_number=None,
                     request_id=0):
        if err is not None:
            self._open_failed(path, request_id)
            self.ctx.ui.set_status("Ошибка")
            self.ctx.ui.log(
                "Ошибка резолва %s: %s" % (path.name, err)
            )
            self.ctx.ui.show_error(
                "Не удалось разрешить %s:\n%s\n\n"
                "Исправьте файл через ПКМ -> Открыть исходный файл"
                % (path.name, err)
            )
            return

        text, line_sources = result
        rel = self._relative(path)

        # Уникальный tab_id — чтобы можно было держать несколько резолвов открытыми
        tab_id = resolved_tab_id(rel)
        self.ctx.documents.open_resolved_tab(
            tab_id,
            "[Res]%s" % rel,
            text,
            line_sources,
            str(path),
            line_number=line_number,
            request_id=request_id or None,
        )
        self.ctx.ui.set_status("Открыт резолв: %s" % rel, 5000)
        self.ctx.ui.log("Резолв готов: %s" % rel)


class ResolvedLtxHandler(FileOpenHandler):
    """Перехватывает открытие .ltx и делегирует его :class:`LtxFileOpener`."""

    priority = 200

    def __init__(self, opener: LtxFileOpener):
        self._opener = opener

    def can_open(self, path):
        return Path(path).suffix.lower() == ".ltx"

    def open(self, path, api):
        return self._opener.open(path)
