# -*- coding: utf-8 -*-
"""
Базовый модуль.

Сценарии:
- «Чистый запуск» (построение дерева + проход Resolver4);
- автоопределение каталога конфигов игровой VFS (./, ../Anomaly 1.5.3 up, ...);
- открытие .ltx через Resolver4 (финальное состояние как в игре);
- контекстные действия дерева.
"""

from pathlib import Path

from sdk import FileOpenHandler, ModuleBase, ModuleManifest
from sdk.events import Events


DEFAULT_CONFIG_DIR = r"H:\GRA2.5\Anomaly 1.5.3 up\gamedata\configs"


class ResolvedLtxHandler(FileOpenHandler):
    """
    Перехватывает открытие .ltx и показывает финальное состояние файла,
    разрешённое через Resolver4 (так, как его видит игра).
    """

    priority = 200

    def __init__(self, module):
        self.module = module

    def can_open(self, path):
        return Path(path).suffix.lower() == ".ltx"

    def open(self, path, api):
        return self.module.open_resolved(path)


class BaseModule(ModuleBase):
    manifest = ModuleManifest(
        name="base",
        version="0.2.0",
        sdk_min="0.1.0",
        description="Базовый сценарий: чистый запуск, VFS-вид, резолв-открытие.",
    )

    # -------------------------------------------------- жизненный цикл
    def init(self, ctx):
        super().init(ctx)

        self.forest = None
        self._running = False

        self.config_dir = self._detect_config_dir(ctx)
        if self.config_dir:
            ctx.log("Каталог конфигов (VFS игры): %s" % self.config_dir)
        else:
            ctx.log("Каталог конфигов не найден — будет запрошен при запуске.")

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
        ctx.commands.register("base.open_raw", self.open_raw)

        # Обработчик открытия .ltx: резолв через Resolver4
        ctx.ui.register_open_handler(ResolvedLtxHandler(self))

        # Контекстное меню дерева
        ctx.ui.register_tree_context_action("Открыть исходный файл (без резолва)", self.open_raw)
        ctx.ui.register_tree_context_action("Открыть в Notepad++", self.open_notepad)
        ctx.ui.register_tree_context_action("Собрать и открыть в Notepad++", self.assemble_open)
        ctx.ui.register_tree_context_action("Копировать относительный путь", self.copy_rel_path)

    def activate(self, ctx):
        ctx.log("Базовый модуль активирован.")

    def deactivate(self, ctx):
        ctx.log("Базовый модуль деактивирован.")

    # -------------------------------------------------- VFS-корень
    def _detect_config_dir(self, ctx):
        candidates = [
            Path.cwd(),                                   # ./
            ctx.paths.app_dir,                            # src/
            ctx.paths.app_dir.parent,                     # ../
            ctx.paths.app_dir.parent.parent / "Anomaly 1.5.3 up",
            Path(DEFAULT_CONFIG_DIR),                     # старый хардкод как fallback
        ]
        return ctx.services.detect_config_dir(candidates)

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

        forest = self.ctx.services.build_tree(scan_dir, system, log=log, clean=True)
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
        self.ctx.ui.log("Дерево построено. Корней: %d; файлов: %d"
                        % (len(forest.roots), forest.total_files))

    # -------------------------------------------------- открытие файлов
    def open_resolved(self, path):
        """Открыть .ltx через Resolver4: финальное состояние как в игре."""
        if self.forest is None:
            self.ctx.ui.show_error("Дерево не построено: сначала «Чистый запуск».")
            return False

        path = Path(path)
        self.ctx.ui.set_status("Резолв: %s…" % path.name)

        def work(log):
            return self.ctx.services.assemble_text(
                path, self.forest, self.config_dir, log=log
            )

        self.ctx.tasks.run(
            work,
            on_done=lambda text, err: self._on_resolved(path, text, err),
            on_log=self.ctx.ui.log,
        )
        return True

    def _on_resolved(self, path, text, err):
        if err is not None:
            self.ctx.ui.set_status("Ошибка")
            self.ctx.ui.show_error("Не удалось разрешить %s:\n%s" % (path.name, err))
            return

        rel = self.ctx.services.rel_path(self.forest, path)
        self.ctx.ui.show_text(
            text,
            "RESOLVED (как в игре): %s" % rel,
            readonly=True,
        )
        self.ctx.ui.set_status("Открыт резолв: %s" % rel, 5000)

    def open_raw(self, path):
        self.ctx.commands.execute("ui.open_file_raw", path)

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