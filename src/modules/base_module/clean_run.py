# -*- coding: utf-8 -*-
"""Сценарий «Пересобрать VFS»: определение VFS и построение дерева."""

from pathlib import Path

from sdk.events import Events

from .settings import config_dir_candidates


class CleanRunFeature:
    """Владеет сценарием сканирования и прохода резолвера."""

    def __init__(self, ctx, session):
        self.ctx = ctx
        self.session = session
        self._running = False
        self._overlay = {}

    # -------------------------------------------------- автоопределение
    def detect_config_dir(self):
        candidates = config_dir_candidates(self.ctx.paths.app_dir)
        return self.ctx.services.detect_config_dir(candidates)

    # -------------------------------------------------- пользовательские действия
    def choose_directory(self):
        directory = self.ctx.ui.ask_directory(str(self.session.config_dir or ""))
        if not directory:
            return

        self.session.config_dir = Path(directory)
        self.run()

    def run(self):
        if self._running:
            self.ctx.ui.log("Пересборка VFS уже выполняется.")
            return

        if self.session.config_dir is None:
            directory = self.ctx.ui.ask_directory("")
            if not directory:
                self.ctx.ui.log("Папка не выбрана — пересборка отменена.")
                return
            self.session.config_dir = Path(directory)

        self._running = True
        self.ctx.ui.set_status("Сканирование…")
        self.ctx.ui.log("Пересборка VFS: %s" % self.session.config_dir)
        self.ctx.events.publish(
            Events.CLEAN_RUN_STARTED,
            str(self.session.config_dir),
        )

        # Снимок черновиков берём в UI-потоке: фоновая задача не должна
        # читать изменяемое состояние проекта.
        overlay = self.ctx.ui.project_overlay()
        self._overlay = overlay
        draft_count = len(getattr(overlay, "source_files", None) or {})
        if draft_count:
            self.ctx.ui.log(
                "Пересборка VFS: черновиков проекта в VFS: %d"
                % draft_count
            )

        self.ctx.tasks.run(
            lambda log: self._scan(log, overlay),
            on_done=self._on_scan_done,
            on_log=self.ctx.ui.log,
        )

    # -------------------------------------------------- фоновая работа
    def _scan(self, log, overlay=None):
        selected = self.session.config_dir

        system = self.ctx.services.find_system_ltx(selected)
        if system is None:
            raise RuntimeError("system.ltx не найден в %s" % selected)

        scan_dir = self.ctx.services.resolve_scan_dir(selected)
        if scan_dir is None:
            raise RuntimeError("Не удалось определить каталог сканирования")

        log("Сканирую: %s" % scan_dir)

        forest = self.ctx.services.build_tree(
            scan_dir,
            system,
            log=log,
            clean=True,
            overlay=overlay,
        )
        self.ctx.services.run_resolver(forest, selected, log=log)
        return forest

    def _on_scan_done(self, forest, err):
        self._running = False

        if err is not None:
            self.ctx.ui.set_status("Ошибка")
            self.ctx.ui.show_error(str(err))
            self.ctx.events.publish(Events.ERROR, str(err))
            return

        self.session.forest = forest
        self.ctx.events.publish(Events.TREE_READY, forest)
        self.ctx.events.publish(Events.CLEAN_RUN_FINISHED, forest)

        try:
            # Предсборка должна видеть тот же слой правок, что и обычный
            # резолв: source_files и resolved_edits. Иначе ключ кэша и
            # вычисленное значение расходятся.
            self.ctx.services.start_prebuild(
                forest,
                self.session.config_dir,
                overlay=self._overlay,
            )
        except Exception as exc:
            self.ctx.ui.log("Не удалось запустить фоновую сборку: %s" % exc)

        summary = self.ctx.services.tree_summary(forest)
        self.ctx.ui.set_status(
            "Корней: %d; файлов LTX: %d; XML: %d; в дереве system: %d; секций: %s"
            % (
                summary.roots,
                summary.total_files,
                summary.xml_files,
                summary.system_tree_files,
                summary.resolver_sections,
            ),
            7000,
        )
        self.ctx.ui.log(
            "Дерево построено. Корней: %d; LTX: %d; XML: %d"
            % (summary.roots, summary.total_files, summary.xml_files)
        )
