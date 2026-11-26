# -*- coding: utf-8 -*-
"""Базовый модуль.

Модуль — композиция LTX/XML-возможностей:

- :class:`LtxFileOpener` — открытие .ltx (резолв/DLTX-патч);
- :class:`XmlFileOpener` — открытие .xml (сырое и после XmlResolver);
- :class:`SectionGraphFeature` — граф связей секции.

Workspace-бутстрап и общие действия дерева/редактора принадлежат
платформе (ADR-33). Все внешние возможности берутся через SDK-контекст
(``ctx.services``, ``ctx.ui``, ``ctx.tasks``). Модуль не импортирует
``core``, ``ui`` и другие модули; PyQt используется только для
собственных окон.
"""

from pathlib import Path

from sdk import ModuleBase
from sdk.contracts.events import Events

from .actions import is_ltx_path, is_xml_path
from .ltx_open import LtxFileOpener, ResolvedLtxHandler
from .section_graph import SectionGraphFeature
from .session import ModuleSession
from .xml_open import XmlFileOpener, XmlOpenHandler


class _SectionGraphProvider:
    """``section.graph`` capability поверхности базового модуля."""

    def __init__(self, ctx, feature):
        self._ctx = ctx
        self._feature = feature

    def register_contributor(self, contributor):
        return self._ctx.services.register_section_graph_contributor(
            contributor
        )

    def unregister_contributor(self, handle) -> None:
        handle.unsubscribe()

    def show(self) -> None:
        self._feature.show()

    def request(self, request):
        return self._ctx.services.collect_section_graph_contribution(
            request
        )


class _LtxViewerProvider:
    def __init__(self, opener):
        self._opener = opener

    def open_document(self, path, line_number=None) -> bool:
        return bool(self._opener.open(path))

    def open_raw_document(self, path, line_number=None) -> bool:
        return bool(self._opener.open_raw(path, line_number))

    def open_resolved_document(self, path, line_number=None) -> bool:
        return bool(
            self._opener.open_resolved_at_line(path, line_number or 0)
        )


class _XmlViewerProvider:
    def __init__(self, opener):
        self._opener = opener

    def open_document(self, path, line_number=None) -> bool:
        return bool(self._opener.open_default(path))

    def open_raw_document(self, path, line_number=None) -> bool:
        return bool(self._opener.open_raw(path))

    def open_resolved_document(self, path, line_number=None) -> bool:
        return bool(self._opener.open_resolved(path, line_number))


class BaseModule(ModuleBase):
    # -------------------------------------------------- жизненный цикл
    def init(self, ctx):
        super().init(ctx)

        self.session = ModuleSession(ctx)
        self._ltx_opener = LtxFileOpener(ctx, self.session)
        self._xml_opener = XmlFileOpener(ctx, self.session)
        self._section_graph = SectionGraphFeature(
            ctx,
            self.session,
            open_raw=self._ltx_opener.open_raw,
        )
        self._config_sub = ctx.events.subscribe(
            Events.CLEAN_RUN_STARTED,
            self._on_config_selected,
        )
        self._theme_sub = ctx.events.subscribe(
            Events.UI_THEME_CHANGED,
            lambda payload: self._on_theme_changed(),
        )

    def _on_config_selected(self, payload=None):
        if payload:
            self.session.config_dir = Path(str(payload))
            self.ctx.log("Каталог конфигов (VFS игры): %s" % payload)

    def _on_theme_changed(self):
        try:
            self._section_graph.refresh_theme()
        except Exception:
            self.ctx.log("base: не удалось обновить тему графа")

    def register(self, ctx):
        self._register_menus(ctx)
        self._register_commands(ctx)
        self._register_open_handler(ctx)
        self._register_tree_actions(ctx)
        ctx.capabilities.provide(
            "section.graph",
            "1.0.0",
            _SectionGraphProvider(ctx, self._section_graph),
        )
        ctx.capabilities.provide(
            "document.viewer.ltx",
            "1.0.0",
            _LtxViewerProvider(self._ltx_opener),
        )
        ctx.capabilities.provide(
            "document.viewer.xml",
            "1.0.0",
            _XmlViewerProvider(self._xml_opener),
        )

    def activate(self, ctx):
        ctx.log("Базовый модуль активирован.")

    def deactivate(self, ctx):
        sub = getattr(self, "_config_sub", None)
        if sub is not None and hasattr(sub, "unsubscribe"):
            sub.unsubscribe()
        self._config_sub = None
        theme_sub = getattr(self, "_theme_sub", None)
        if theme_sub is not None and hasattr(theme_sub, "unsubscribe"):
            theme_sub.unsubscribe()
        self._theme_sub = None
        self._section_graph.close()
        ctx.log("Базовый модуль деактивирован.")

    # -------------------------------------------------- регистрации
    def _register_menus(self, ctx):
        ctx.ui.register_menu(
            "Инструменты",
            "Связи секции",
            self._section_graph.show,
            "Ctrl+G",
        )

    def _register_commands(self, ctx):
        commands = ctx.commands
        commands.register("base.assemble_open", self._assemble_and_open)
        commands.register(
            "base.open_ltx_resolved_at_line",
            self._ltx_opener.open_resolved_at_line,
        )
        commands.register(
            "base.open_xml_resolved",
            self._xml_opener.open_resolved,
        )
        commands.register(
            "base.open_xml_resolved_at_line",
            self._xml_opener.open_resolved_at_line,
        )
        commands.register(
            "base.open_xml_resolved_notepad",
            self._xml_opener.open_resolved_in_external_editor,
        )
        commands.register("base.section_graph", self._section_graph.show)

    def _register_open_handler(self, ctx):
        ctx.ui.register_open_handler(ResolvedLtxHandler(self._ltx_opener))
        ctx.ui.register_open_handler(XmlOpenHandler(self._xml_opener))

    def _assemble_and_open(self, path):
        forest = self.ctx.ui.workspace_forest()
        if forest is None:
            self.ctx.ui.show_error(
                "Нет дерева: сначала выполните «Пересобрать VFS»."
            )
            return

        path = Path(path)
        self.ctx.ui.set_status("Сборка: %s…" % path.name)
        self.ctx.ui.log("Сборка: %s" % path.name)

        def work(log):
            out = self.ctx.services.assemble_to_file(path, forest)
            ok, message = self.ctx.services.open_external_editor(out)
            if not ok:
                raise RuntimeError(message)
            return out

        def done(out, err):
            if err is not None:
                self.ctx.ui.set_status("Ошибка")
                self.ctx.ui.log("Ошибка сборки: %s" % err)
                self.ctx.ui.show_error(str(err))
                return
            self.ctx.ui.set_status("Открыт: %s" % Path(out).name, 5000)
            self.ctx.ui.log("Сборка открыта: %s" % Path(out).name)

        self.ctx.tasks.run(
            work,
            on_done=done,
            on_log=self.ctx.ui.log,
            operation_key="base.assemble:%s" % self.ctx.paths.key(path),
            title="Сборка: %s" % path.name,
        )

    def _register_tree_actions(self, ctx):
        ui = ctx.ui

        # LTX-путь: специфичные для формата действия.
        ui.register_tree_context_action(
            "Открыть исходный файл (без резолва)",
            self._ltx_opener.open_raw,
            predicate=is_ltx_path,
        )
        ui.register_tree_context_action(
            "Собрать и открыть в Notepad++",
            self._assemble_and_open,
            predicate=is_ltx_path,
        )

        # XML-путь: include-сборка + DXML.
        ui.register_tree_context_action(
            "Открыть исходный XML",
            self._xml_opener.open_raw,
            predicate=is_xml_path,
        )
        ui.register_tree_context_action(
            "Открыть обработанный XML",
            self._xml_opener.open_resolved,
            predicate=is_xml_path,
        )
        ui.register_tree_context_action(
            "Открыть обработанный XML в Notepad++",
            self._xml_opener.open_resolved_in_external_editor,
            predicate=is_xml_path,
        )

        # Действия для контекстного меню вкладок.
        ui.register_file_action(
            "Открыть исходный",
            self._ltx_opener.open_raw,
            predicate=is_ltx_path,
        )
        ui.register_file_action(
            "Открыть исходный",
            self._xml_opener.open_raw,
            predicate=is_xml_path,
        )
        ui.register_file_action(
            "Открыть resolved",
            self.ctx.ui.open_file,
            predicate=is_ltx_path,
        )
        ui.register_file_action(
            "Открыть resolved",
            self._xml_opener.open_resolved,
            predicate=is_xml_path,
        )
        ui.register_file_action(
            "Открыть resolved в Notepad++",
            self._assemble_and_open,
            predicate=is_ltx_path,
        )
        ui.register_file_action(
            "Открыть resolved в Notepad++",
            self._xml_opener.open_resolved_in_external_editor,
            predicate=is_xml_path,
        )


MODULE = BaseModule()
