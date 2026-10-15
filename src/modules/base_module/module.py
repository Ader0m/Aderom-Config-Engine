# -*- coding: utf-8 -*-
"""
Базовый модуль.

Здесь живёт базовый пользовательский сценарий, который раньше
ошибочно находился в UI-платформе:

- «Чистый запуск»;
- выбор рабочей папки конфигов;
- построение базового дерева;
- проход Resolver4 по system.ltx;
- контекстные действия дерева (Notepad++, сборка, копирование пути).

Модуль не импортирует core и ui. Всё — только через ctx (SDK).
"""

from pathlib import Path

from sdk import ModuleBase, ModuleManifest
from sdk.events import Events


DEFAULT_CONFIG_DIR = r"H:\GRA2.5\Anomaly 1.5.3 up\gamedata\configs"


class BaseModule(ModuleBase):
    manifest = ModuleManifest(
        name="base",
        version="0.1.0",
        sdk_min="0.1.0",
        description="Базовый сценарий: чистый запуск, дерево, внешние редакторы.",
    )

    # -------------------------------------------------- жизненный цикл
    def init(self, ctx):
        super().init(ctx)

        self.forest = None
        self._running = False

        self.config_dir = Path(DEFAULT_CONFIG_DIR)
        if not self.config_dir.is_dir():
            self.config_dir = None

        ctx.events.subscribe(Events.APP_STARTED, lambda payload: self.clean_run())

    def register(self, ctx):
        # Меню
        ctx.ui.register_menu("Файл", "Открыть папку...", self.choose_dir, "Ctrl+O")
        ctx.ui.register_menu("Файл", "Чистый запуск", self.clean_run, "Ctrl+Shift+R")

        # Команды
        ctx.commands.register("base.choose_dir", self.choose_dir)
        ctx.commands.register("base.clean_run", self.clean_run)
        ctx.commands.register("base.open_notepad", self.open_notepad)
        ctx.commands.register("base.assemble_open", self.assemble_open)
        ctx.commands.register("base.copy_rel_path", self.copy_rel_path)

        # Контекстное меню дерева
        ctx.ui.register_tree_context_action("Открыть в Notepad++", self.open_notepad)
        ctx.ui.register_tree_context_action(
            "Собрать и открыть в Notepad++", self.assemble_open
        )
        ctx.ui.register_tree_context_action("Копировать относительный путь", self.copy_rel_path)

    def activate(self, ctx):
        ctx.log("Базовый модуль активирован.")

    def deactivate(self, ctx):
        ctx.log("Базовый модуль деактивирован.")

    # -------------------------------------------------- сценарий запуска
    def choose_dir(self):
        d = self.ctx.ui.ask_directory(str(self.config_dir or ""))
        if d:
            self.config_dir = Path(d)
            self.clean_run()

    def clean_run(self):
        if self._running:
            self.ctx.ui.log("Чистый запуск уже выполняется.")
            return

        if not self.config_dir or not self.config_dir.is_dir():
            d = self.ctx.ui.ask_directory("")
            if not d:
                self.ctx.ui.log("Папка не выбрана — запуск отменён.")
                return
            self.config_dir = Path(d)

        self._running = True
        self.ctx.ui.set_status("Сканирование…")
        self.ctx.ui.log("Чистый запуск: %s" % self.config_dir)
        self.ctx.events.publish(Events.CLEAN_RUN_STARTED, str(self.config_dir))

        self.ctx.tasks.run(
            self._scan,
            on_done=self._on_scan_done,
            on_log=self.ctx.ui.log,
        )

    def _scan(self, log):
        sel = self.config_dir

        system = self.ctx.services.find_system_ltx(sel)
        if system is None:
            raise RuntimeError("system.ltx не найден в %s" % sel)

        scan_dir = sel if (sel / "system.ltx").is_file() else system.parent
        log("Сканирую: %s" % scan_dir)

        forest = self.ctx.services.build_tree(
            scan_dir,
            system,
            log=log,
            clean=True,
        )

        self.ctx.services.resolver_pass(forest, self.config_dir, log=log)

        return forest

    def _on_scan_done(self, forest, err):
        self._running = False

        if err is not None:
            self.ctx.ui.set_status("Ошибка")
            self.ctx.ui.show_error(str(err))
            self.ctx.events.publish(Events.ERROR, str(err))
            return

        self.forest = forest

        self.ctx.events.publish(Events.TREE_READY, forest)
        self.ctx.events.publish(Events.CLEAN_RUN_FINISHED, forest)

        self.ctx.ui.set_status(
            "Корней: %d; файлов: %d; в дереве system: %d; секций: %s"
            % (
                len(forest.roots),
                forest.total_files,
                forest.system_tree_files,
                forest.resolver_sections,
            ),
            7000,
        )
        self.ctx.ui.log(
            "Дерево построено. Корней: %d; файлов: %d"
            % (len(forest.roots), forest.total_files)
        )

    # -------------------------------------------------- контекстные действия
    def open_notepad(self, path):
        ok, msg = self.ctx.services.open_notepad(path)
        if not ok:
            self.ctx.ui.show_error(msg)

    def assemble_open(self, path):
        if self.forest is None:
            self.ctx.ui.show_error("Нет дерева: сначала выполните «Чистый запуск».")
            return

        self.ctx.ui.set_status("Сборка: %s…" % Path(path).name)

        def work(log):
            sm = self.ctx.services.source_map(self.forest)
            out = self.ctx.services.assemble_to_file(path, sm)
            ok, msg = self.ctx.services.open_notepad(out)
            if not ok:
                raise RuntimeError(msg)
            return out

        self.ctx.tasks.run(work, on_done=self._on_assemble_done, on_log=self.ctx.ui.log)

    def _on_assemble_done(self, out, err):
        if err is not None:
            self.ctx.ui.set_status("Ошибка")
            self.ctx.ui.show_error(str(err))
            return
        self.ctx.ui.set_status("Открыт: %s" % Path(out).name, 5000)

    def copy_rel_path(self, path):
        if self.forest is None:
            text = str(path)
        else:
            text = self.ctx.services.rel_path(self.forest, Path(path))
        self.ctx.ui.clipboard_set(text)


MODULE = BaseModule()