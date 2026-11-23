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
import shutil
import sys
import time
import traceback
from pathlib import Path

# Открытый файл диагностики держим на уровне модуля: faulthandler не
# копирует дескриптор и должен иметь живой объект.
_CRASH_LOG = None


def _application_root(app_dir):
    """Каталог portable-приложения, рядом с которым лежат данные.

    В исходной раскладке код лежит в ``<root>/src``, поэтому данные
    должны быть в ``<root>``. В замороженной сборке — рядом с exe.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    app_dir = Path(app_dir).resolve()
    if app_dir.name.lower() == "src":
        return app_dir.parent
    return app_dir


def _migrate_generated_dir(old_dir, new_dir):
    """Один раз перенести старый ``src/generated`` в portable-корень."""
    old_dir = Path(old_dir)
    new_dir = Path(new_dir)
    if old_dir == new_dir or not old_dir.is_dir() or new_dir.exists():
        return
    try:
        new_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old_dir), str(new_dir))
    except OSError:
        pass


def _remove_legacy_caches(generated_dir):
    """Удалить кэши старого формата: миграции нет, только пересборка."""
    removed = []
    for name in ("resolved_cache", "item_cache"):
        path = Path(generated_dir) / name
        if not path.exists():
            continue
        try:
            shutil.rmtree(str(path))
            removed.append(name)
        except OSError:
            pass
    return removed


def _install_crash_log(generated_dir):
    """Писать необработанные исключения и аварийные стеки в файл.

    PyQt5 при исключении в слоте вызывает qFatal() — процесс падает без
    внятного сообщения, а stderr под Mod Organizer не виден. Этот лог
    сохраняет traceback до аварийного завершения.
    """
    global _CRASH_LOG

    log_path = Path(generated_dir) / "crash.log"
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

    from app.patching.audit import PatchAuditLog
    from app.paths import PathsAdapter
    from app.workspace import SnapshotService
    from core.api.services import CoreServices
    from core.cache.service import CacheService
    from core.tools.cache import (
        CACHE_FORMAT_VERSION,
        FileCacheSystem,
        ResolvedCache,
    )
    from core.tree.builder import TreeBuilder
    from core.tree.query import TreeQueryService
    from core.tree.graph import ReferenceGraphBuilder, DisabledCache
    from core.resolver.service import ResolverService
    from core.resolver.assembly import AssemblyService
    from core.xml.service import XmlResolveService

    from sdk import SDK_VERSION, AppPaths
    from ui.module_loader import ModuleLoader
    from ui.platform.sdk_bridge import build_module_context
    from sdk.contracts.events import Events
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
    from ui.theme import apply_theme, configure_cache_dir

    # Portable-раскладка: код может лежать в src/, но все данные
    # приложения — generated/projects — находятся рядом с приложением.
    app_dir = Path(__file__).resolve().parent
    app_root = _application_root(app_dir)
    generated_dir = app_root / "generated"
    projects_dir = app_root / "projects"
    _migrate_generated_dir(app_dir / "generated", generated_dir)
    generated_dir.mkdir(parents=True, exist_ok=True)
    removed_caches = _remove_legacy_caches(generated_dir)

    from core.tools.vfs import configure_materialize_root

    configure_materialize_root(generated_dir / "assets")
    configure_cache_dir(generated_dir / "theme_icons")

    app = QApplication(sys.argv)
    app.setApplicationName("LTX Explorer")
    app.setApplicationVersion("0.4.0")
    apply_theme(app)

    _install_crash_log(generated_dir)

    # 1) Сервисы ядра.
     # --------------------------------------------------
    # 1) Сервисы ядра (Композиционный корень создаёт их здесь)
    # --------------------------------------------------

    # Общий кэш
    cache = FileCacheSystem()
    snapshot_service = SnapshotService()

    # Дерево
    resolved_cache = ResolvedCache(
        cache_dir=generated_dir / "cache-v2" / "resolved",
        max_entries=128,
        max_chars=256_000_000,
        # Один файл кэша — это один корень/XML. Рабочий набор большой
        # VFS может быть больше тысяч файлов, поэтому ограничиваем не
        # количество, а общий объём (с запасом; типичный кэш — десятки МБ).
        max_disk_bytes=1_000_000_000,
        format_version=CACHE_FORMAT_VERSION,
    )
    cache_service = CacheService(
        resolved_cache=resolved_cache,
        format_version=CACHE_FORMAT_VERSION,
    )
    tree_builder = TreeBuilder(
        cache=cache,
        cache_service=cache_service,
    )
    tree_query = TreeQueryService()

    # Граф связей
    graph_builder = ReferenceGraphBuilder(cache=DisabledCache())

    # Резолвер и сборка
    resolver_service = ResolverService(
        cache=cache,
        resolved_cache=resolved_cache,
        cache_service=cache_service,
    )
    xml_resolve_service = XmlResolveService(
        resolved_cache=resolved_cache,
        cache_service=cache_service,
    )
    assembly_service = AssemblyService(
        resolver_service=resolver_service,
        generated_dir=generated_dir,
    )

    # Фасад (тонкий, только агрегирует сервисы)
    services = CoreServices(
        cache=cache,
        tree_builder=tree_builder,
        tree_query=tree_query,
        graph_builder=graph_builder,
        resolver_service=resolver_service,
        assembly_service=assembly_service,
        generated_dir=generated_dir,
        xml_resolve_service=xml_resolve_service,
        cache_service=cache_service,
        snapshot_service=snapshot_service,
    )
    services.patch_commit.audit = PatchAuditLog.for_session(
        generated_dir,
    )

    # 2) Сервисы платформы.
    events = EventBus()
    commands = CommandBus()
    tasks = TaskService()
    app_paths = AppPaths(
        app_dir=app_dir,
        generated_dir=generated_dir,
        projects_dir=projects_dir,
    )
    paths = PathsAdapter(app_paths)

    api = PlatformApi(
        events,
        commands,
        tasks,
        services,
        paths,
        snapshot_service=snapshot_service,
    )
    if removed_caches:
        api.log(
            "Кэш старого формата удалён: %s (пересборка при первом обращении)"
            % ", ".join(removed_caches)
        )

    # Движок подсказок: провайдеры регистрируются в composition root,
    # модули только подключают его к своим текстовым полям через SDK.
    hints = HintService(
        services=services,
        events=events,
        tasks=tasks,
        # Подсказкам нужен сырой лес: они работают внутри платформы,
        # а не через публичный SDK-контракт.
        forest_getter=lambda: api.workspace.forest,
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
        # LTX-провайдеры регистрируются всегда, но временно отключены
        # флагом LTX_HINTS_ENABLED (ui/hints/providers/flags.py).
        hints.register(provider_type(hints.catalogs))
    api.hints = hints

    # 3) Загрузка модулей (до построения окна, чтобы регистрации попали в меню).
    def make_context(manifest):
        return build_module_context(
            manifest,
            sdk_version=SDK_VERSION,
            events=events,
            commands=commands,
            ui=api,
            tasks=tasks,
            services=services,
            paths=paths,
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
    shutdown_tasks = getattr(tasks, "shutdown", None)
    if callable(shutdown_tasks):
        if not shutdown_tasks():
            api.log(
                "Не все фоновые задачи завершились за отведённое время"
            )
    sys.exit(code)


if __name__ == "__main__":
    _dpi()
    main()