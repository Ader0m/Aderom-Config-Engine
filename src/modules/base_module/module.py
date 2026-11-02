# -*- coding: utf-8 -*-
"""Базовый модуль.

Модуль — только композиция возможностей:

- :class:`CleanRunFeature` — сценарий построения дерева;
- :class:`LtxFileOpener` — открытие .ltx (резолв/DLTX-патч);
- :class:`SectionGraphFeature` — граф связей секции;
- :class:`TreeActions` — действия дерева;
- :class:`EditorActions` — действия редактора.

Все внешние возможности берутся через SDK-контекст (``ctx.services``,
``ctx.ui``, ``ctx.tasks``). Модуль не импортирует ``core`` и ``ui``.
PyQt используется только для собственных окон модуля; платформа
предоставляет для них хостинг через ``ctx.ui.show_module_window``.
"""

from sdk import ModuleBase, ModuleManifest
from sdk.events import Events

from .actions import EditorActions, TreeActions
from .clean_run import CleanRunFeature
from .ltx_open import LtxFileOpener, ResolvedLtxHandler
from .section_graph import SectionGraphFeature
from .session import ModuleSession


class BaseModule(ModuleBase):
    manifest = ModuleManifest(
        name="base",
        version="0.3.0",
        sdk_min="0.1.0",
        description="Базовый сценарий: чистый запуск, VFS-вид, резолв-открытие.",
    )

    # -------------------------------------------------- жизненный цикл
    def init(self, ctx):
        super().init(ctx)

        self.session = ModuleSession()
        self._clean_run = CleanRunFeature(ctx, self.session)
        self._ltx_opener = LtxFileOpener(ctx, self.session)
        self._section_graph = SectionGraphFeature(ctx, self.session)
        self._tree_actions = TreeActions(ctx, self.session)
        self._editor_actions = EditorActions(ctx)

        self.session.config_dir = self._clean_run.detect_config_dir()
        if self.session.config_dir:
            ctx.log("Каталог конфигов (VFS игры): %s" % self.session.config_dir)
        else:
            ctx.log("Каталог конфигов не найден — будет запрошен при запуске.")

        self._app_started_sub = ctx.events.subscribe(
            Events.APP_STARTED,
            lambda payload: self._clean_run.run(),
        )

    def register(self, ctx):
        self._register_menus(ctx)
        self._register_commands(ctx)
        self._register_open_handler(ctx)
        self._register_tree_actions(ctx)

    def activate(self, ctx):
        ctx.log("Базовый модуль активирован.")

    def deactivate(self, ctx):
        sub = getattr(self, "_app_started_sub", None)
        if sub is not None and hasattr(sub, "unsubscribe"):
            sub.unsubscribe()
        self._app_started_sub = None
        self._section_graph.close()
        ctx.log("Базовый модуль деактивирован.")

    # -------------------------------------------------- регистрации
    def _register_menus(self, ctx):
        ui = ctx.ui

        ui.register_menu(
            "Файл", "Открыть папку...",
            self._clean_run.choose_directory, "Ctrl+O",
        )
        ui.register_menu(
            "Файл", "Чистый запуск",
            self._clean_run.run, "Ctrl+Shift+R",
        )
        ui.register_menu(
            "Инструменты", "Граф связей секции",
            self._section_graph.show, "Ctrl+G",
        )
        ui.register_menu(
            "Вид", "Сортировать по убыванию (Файлов в дереве)",
            self._tree_actions.sort_tree_by_files_desc,
        )
        ui.register_menu("Правка", "Отменить", self._editor_actions.undo)
        ui.register_menu("Правка", "Повторить", self._editor_actions.redo)
        ui.register_menu("Правка", "Найти в файле", self._editor_actions.find)

    def _register_commands(self, ctx):
        commands = ctx.commands

        commands.register("base.choose_dir", self._clean_run.choose_directory)
        commands.register("base.clean_run", self._clean_run.run)
        commands.register("base.open_notepad", self._tree_actions.open_in_external_editor)
        commands.register("base.assemble_open", self._tree_actions.assemble_and_open)
        commands.register("base.copy_rel_path", self._tree_actions.copy_relative_path)
        commands.register("base.open_raw", self._tree_actions.open_raw)
        commands.register("base.edit_undo", self._editor_actions.undo)
        commands.register("base.edit_redo", self._editor_actions.redo)
        commands.register("base.edit_find", self._editor_actions.find)
        commands.register("base.section_graph", self._section_graph.show)
        commands.register("base.sort_tree_files_desc", self._tree_actions.sort_tree_by_files_desc)

    def _register_open_handler(self, ctx):
        ctx.ui.register_open_handler(ResolvedLtxHandler(self._ltx_opener))

    def _register_tree_actions(self, ctx):
        ui = ctx.ui
        actions = self._tree_actions

        ui.register_tree_context_action("Открыть исходный файл (без резолва)", actions.open_raw)
        ui.register_tree_context_action("Открыть в Notepad++", actions.open_in_external_editor)
        ui.register_tree_context_action("Собрать и открыть в Notepad++", actions.assemble_and_open)
        ui.register_tree_context_action("Копировать относительный путь", actions.copy_relative_path)


MODULE = BaseModule()
