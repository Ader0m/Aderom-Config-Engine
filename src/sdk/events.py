# -*- coding: utf-8 -*-
"""Имена системных событий и UI-действий."""

class Events:
    # -------------------------------------------------- жизненный цикл
    APP_STARTED = "app_started"
    TREE_READY = "tree_ready"
    # Файл действительно открыт: виджет размещён и стал текущим (payload — путь).
    FILE_OPENED = "file_opened"
    # Открытие не состоялось (payload — путь): отложенная навигация сбрасывается.
    FILE_OPEN_FAILED = "file_open_failed"
    CLEAN_RUN_STARTED = "clean_run_started"
    CLEAN_RUN_FINISHED = "clean_run_finished"
    ERROR = "error"

    # -------------------------------------------------- UI-факты
    # Публикуются платформой после того, как действие уже выполнено.
    UI_WINDOW_SHOWN = "ui.window.shown"
    UI_WINDOW_CLOSED = "ui.window.closed"
    UI_TAB_OPENED = "ui.tab.opened"
    UI_TAB_CLOSED = "ui.tab.closed"
    UI_TAB_CHANGED = "ui.tab.changed"

    # -------------------------------------------------- UI-действия
    # Рассылаются через EventBus.dispatch и могут быть перехвачены.
    UI_WINDOW_REQUESTED = "ui.window.requested"
    UI_TAB_REQUESTED = "ui.tab.requested"
    UI_FILE_OPEN_REQUESTED = "ui.file.open_requested"
    UI_SHORTCUT = "ui.shortcut"
    UI_MOUSE = "ui.mouse"
