# -*- coding: utf-8 -*-
"""
Композиционный корень приложения.

Порядок запуска по архитектуре (8.1):
1. QApplication и тема;
2. сервисы ядра;
3. шины, фоновые задачи, platform api;
4. загрузка и регистрация модулей;
5. построение главного окна;
6. активация модулей;
7. событие APP_STARTED.
"""

import ctypes
import sys
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

# Открытый файл диагностики держим на уровне модуля: faulthandler не
# копирует дескриптор и должен иметь живой объект.
_CRASH_LOG = None


def _install_crash_log(app_dir):
    """Писать необработанные исключения и аварийные стеки в файл.

    PyQt5 при исключении в слоте вызывает qFatal() — процесс падает без
    внятного сообщения, а stderr под Mod Organizer не виден. Этот лог
    сохраняет traceback до аварийного завершения.
    """
    global _CRASH_LOG

    log_path = Path(app_dir) / "generated" / "crash.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    def hook(exc_type, exc, tb):
        try:
            with log_path.open("a", encoding="utf-8") as stream:
                stream.write(
                    "\n=== %s ===\n"
                    % time.strftime("%Y-%m-%d %H:%M:%S")
                )
                stream.write("".join(
                    traceback.format_exception(exc_type, exc, tb)
                ))
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = hook

    try:
        import faulthandler

        _CRASH_LOG = log_path.open("a", encoding="utf-8")
        faulthandler.enable(file=_CRASH_LOG, all_threads=True)
    except Exception:
        _CRASH_LOG = None


def _dpi():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def main():
    from PyQt5.QtWidgets import QApplication

    from core.facade import CoreServices
    from core.tools.cache import FileCacheSystem
    from core.tree.builder import TreeBuilder
    from core.tree.query import TreeQueryService
    from core.tree.graph import ReferenceGraphBuilder, DisabledCache
    from core.resolver.service import ResolverService
    from core.resolver.assembly import AssemblyService
    from core.tools.cache import ResolvedCache
    from core.xml.service import XmlResolveService

    from sdk import SDK_VERSION, ModuleContext
    from ui.module_loader import ModuleLoader
    from sdk.events import Events
    from ui.bus import CommandBus, EventBus
    from ui.hints import HintService
    from ui.hints.providers import (
        ColorTagProvider,
        DirectiveProvider,
        IncludePathProvider,
        KeyProvider,
        LocalizationKeyProvider,
        ParentProvider,
        SectionProvider,
    )
    from ui.main_window import MainWindow
    from ui.platform_api import PlatformApi
    from ui.tasks import TaskService
    from ui.theme import apply_theme

    app = QApplication(sys.argv)
    app.setApplicationName("LTX Explorer")
    app.setApplicationVersion("0.2.0")
    apply_theme(app)

    app_dir = Path(__file__).resolve().parent
    _install_crash_log(app_dir)

    # 1) Сервисы ядра.
     # --------------------------------------------------
    # 1) Сервисы ядра (Композиционный корень создаёт их здесь)
    # --------------------------------------------------

    # Общий кэш
    cache = FileCacheSystem()

    # Дерево
    tree_builder = TreeBuilder(cache=cache)
    tree_query = TreeQueryService()

    # Граф связей
    graph_builder = ReferenceGraphBuilder(cache=DisabledCache())

    # Резолвер и сборка
    resolved_cache = ResolvedCache(
        cache_dir=app_dir / "generated" / "resolved_cache",
        max_entries=128,
        max_chars=256_000_000,
        # Один файл кэша — это один корень/XML. Рабочий набор большой
        # VFS может быть больше тысяч файлов, поэтому ограничиваем не
        # количество, а общий объём (с запасом; типичный кэш — десятки МБ).
        max_disk_bytes=1_000_000_000,
    )
    resolver_service = ResolverService(
        cache=cache,
        resolved_cache=resolved_cache,
    )
    xml_resolve_service = XmlResolveService(resolved_cache=resolved_cache)
    assembly_service = AssemblyService(
        resolver_service=resolver_service,
        generated_dir=app_dir / "generated",
    )

    # Фасад (тонкий, только агрегирует сервисы)
    services = CoreServices(
        cache=cache,
        tree_builder=tree_builder,
        tree_query=tree_query,
        graph_builder=graph_builder,
        resolver_service=resolver_service,
        assembly_service=assembly_service,
        generated_dir=app_dir / "generated",
        xml_resolve_service=xml_resolve_service,
    )

    # 2) Сервисы платформы.
    events = EventBus()
    commands = CommandBus()
    tasks = TaskService()
    paths = SimpleNamespace(
        app_dir=app_dir,
        generated_dir=services.generated_dir,
        projects_dir=app_dir.parent / "projects",
    )

    api = PlatformApi(events, commands, tasks, services, paths)

    # Движок подсказок: провайдеры регистрируются в composition root,
    # модули только подключают его к своим текстовым полям через SDK.
    hints = HintService(
        services=services,
        events=events,
        tasks=tasks,
        forest_getter=api.workspace_forest,
        overlay_getter=api.project_overlay,
        log_fn=api.log,
    )
    for provider_type in (
        IncludePathProvider,
        ColorTagProvider,
        LocalizationKeyProvider,
        SectionProvider,
        ParentProvider,
        KeyProvider,
        DirectiveProvider,
    ):
        hints.register(provider_type(hints.catalogs))
    api.hints = hints

    # 3) Загрузка модулей (до построения окна, чтобы регистрации попали в меню).
    def make_context(manifest):
        return ModuleContext(
            SDK_VERSION,
            events,
            commands,
            api,
            tasks,
            services,
            paths,
            module_name=manifest.name,
        )

    loader = ModuleLoader(make_context)
    loader.discover_and_load(log=api.log)

    # 4) Главное окно. Сразу разворачиваем: рабочая среда рассчитана
    # на широкий экран (дерево + редактор + диагностика).
    window = MainWindow(api)
    window.showMaximized()

    # 5) Активация модулей и старт.
    loader.activate_all(log=api.log)
    events.publish(Events.APP_STARTED)

    code = app.exec_()

    services.stop_prebuild()
    hints.close()
    loader.deactivate_all(log=api.log)
    sys.exit(code)


if __name__ == "__main__":
    _dpi()
    main()