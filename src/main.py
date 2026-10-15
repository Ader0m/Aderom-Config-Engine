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
from pathlib import Path
from types import SimpleNamespace


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
    from sdk import SDK_VERSION, ModuleContext, ModuleLoader
    from sdk.events import Events
    from ui.bus import CommandBus, EventBus
    from ui.main_window import MainWindow
    from ui.platform_api import PlatformApi
    from ui.tasks import TaskService
    from ui.theme import apply_theme

    app = QApplication(sys.argv)
    app.setApplicationName("LTX Explorer")
    app.setApplicationVersion("0.2.0")
    apply_theme(app)

    app_dir = Path(__file__).resolve().parent

    # 1) Сервисы ядра.
    services = CoreServices(generated_dir=app_dir / "generated")

    # 2) Сервисы платформы.
    events = EventBus()
    commands = CommandBus()
    tasks = TaskService()
    paths = SimpleNamespace(app_dir=app_dir, generated_dir=services.generated_dir)

    api = PlatformApi(events, commands, tasks, services, paths)

    # 3) Загрузка модулей (до построения окна, чтобы регистрации попали в меню).
    def make_context(manifest):
        return ModuleContext(
            SDK_VERSION, events, commands, api, tasks, services, paths
        )

    loader = ModuleLoader(make_context)
    loader.discover_and_load(log=api.log)

    # 4) Главное окно.
    window = MainWindow(api)
    window.show()

    # 5) Активация модулей и старт.
    loader.activate_all(log=api.log)
    events.publish(Events.APP_STARTED)

    code = app.exec_()

    loader.deactivate_all(log=api.log)
    sys.exit(code)


if __name__ == "__main__":
    _dpi()
    main()