# -*- coding: utf-8 -*-
"""Платформенный сценарий workspace: выбор VFS и пересборка дерева.

Каталог игры, сканирование и построение леса больше не принадлежат
модулю ``base`` (ADR-33). Сценарий владеет платформа и публикует те же
события, что и раньше.
"""

from __future__ import annotations

from pathlib import Path

from sdk.contracts.events import Events
from ui.platform.tree_summary import summarize_forest

UPSTREAM_GAME_DIR = Path("Anomaly 1.5.3 up") / "gamedata"
MO2_GAMEDATA_DIR = Path("gamedata")


def _app_root(app_dir: Path) -> Path:
    app_dir = Path(app_dir)
    if app_dir.name.lower() == "src":
        return app_dir.parent
    return app_dir


def config_dir_candidates(app_dir) -> list[Path]:
    """Каталоги, где может лежать игровая VFS с ``system.ltx``."""
    app_dir = Path(app_dir).resolve()
    cwd = Path.cwd()
    app_root = _app_root(app_dir)

    candidates = [
        cwd / MO2_GAMEDATA_DIR,
        cwd,
        app_root / MO2_GAMEDATA_DIR,
        app_root,
        app_root.parent / UPSTREAM_GAME_DIR,
        app_root.parent / UPSTREAM_GAME_DIR.parent,
        app_dir,
        app_dir.parent,
        app_dir.parent.parent,
    ]

    for base in (cwd, app_root, app_root.parent):
        if base == base.parent:
            continue
        try:
            entries = list(base.iterdir())
        except OSError:
            continue
        for child in entries:
            try:
                if (
                    child.is_dir()
                    and (child / "gamedata" / "configs" / "system.ltx").is_file()
                ):
                    candidates.append(child)
            except OSError:
                continue

    result = []
    seen = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


class WorkspaceActions:
    """Выбор каталога игры и пересборка VFS на платформе."""

    def __init__(self, api, services, tasks, events):
        self.api = api
        self.services = services
        self.tasks = tasks
        self.events = events
        self.config_dir = None
        self._overlay = {}
        self._app_started_sub = events.subscribe(
            Events.APP_STARTED,
            lambda _payload: self.run(),
        )

    def detect_config_dir(self):
        candidates = config_dir_candidates(self.api.paths.app_dir)
        return self.services.resolve.detect_config_dir(candidates)

    def choose_directory(self):
        directory = self.api.ask_directory(str(self.config_dir or ""))
        if not directory:
            return
        self.config_dir = Path(directory)
        self.run()

    def run(self):
        if self.tasks.operations.is_active("vfs.rebuild"):
            self.api.log("Пересборка VFS уже выполняется.")
            return

        if self.config_dir is None:
            directory = self.api.ask_directory("")
            if not directory:
                self.api.log("Папка не выбрана — пересборка отменена.")
                return
            self.config_dir = Path(directory)

        self.api.set_status("Сканирование…")
        self.api.log("Пересборка VFS: %s" % self.config_dir)
        self.events.publish(
            Events.CLEAN_RUN_STARTED,
            str(self.config_dir),
        )

        overlay = self.api.project_overlay()
        self._overlay = overlay
        draft_count = len(getattr(overlay, "source_files", None) or {})
        if draft_count:
            self.api.log(
                "Пересборка VFS: черновиков проекта в VFS: %d"
                % draft_count
            )

        self.tasks.run(
            lambda log: self._scan(log, overlay),
            on_done=self._on_scan_done,
            on_log=self.api.log,
            operation_key="vfs.rebuild",
            title="Пересборка VFS",
        )

    def _scan(self, log, overlay=None):
        selected = self.config_dir
        system = self.services.tree.find_system_ltx(selected)
        if system is None:
            raise RuntimeError("system.ltx не найден в %s" % selected)

        scan_dir = self.services.tree.resolve_scan_dir(selected)
        if scan_dir is None:
            raise RuntimeError("Не удалось определить каталог сканирования")

        log("Сканирую: %s" % scan_dir)
        forest = self.services.tree.build_tree(
            scan_dir,
            system,
            log=log,
            clean=True,
            overlay=overlay,
        )
        self.services.resolve.run_resolver(forest, selected, log=log)
        return forest

    def _on_scan_done(self, forest, err):
        if err is not None:
            if getattr(self.api, "_closing", False):
                self.api.log("Пересборка VFS прервана при закрытии")
                return
            self.api.set_status("Ошибка")
            self.api.show_error(str(err))
            self.events.publish(Events.ERROR, str(err))
            return

        ready = self.api.publish_tree_ready(forest)
        self.events.publish(Events.CLEAN_RUN_FINISHED, ready)

        try:
            self.services.prebuild.start(
                forest,
                self.config_dir,
                overlay=self._overlay,
            )
        except Exception as exc:
            self.api.log("Не удалось запустить фоновую сборку: %s" % exc)

        summary = summarize_forest(forest)
        self.api.set_status(
            "Корней: %d; файлов LTX: %d; XML: %d; "
            "в дереве system: %d; секций: %s"
            % (
                summary.roots,
                summary.total_files,
                summary.xml_files,
                summary.system_tree_files,
                summary.resolver_sections,
            ),
            7000,
        )
        self.api.log(
            "Дерево построено. Корней: %d; LTX: %d; XML: %d"
            % (summary.roots, summary.total_files, summary.xml_files)
        )
