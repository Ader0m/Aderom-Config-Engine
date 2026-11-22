# -*- coding: utf-8 -*-
"""Имена системных событий и UI-действий.

Политика namespace: ``<owner>.<name>``.

- ``app.*`` — жизненный цикл приложения;
- ``ui.*`` — факты/действия платформы (включая проект и workspace);
- ``<module>.*`` — события конкретного модуля.
"""

# Владелец платформенных подписок. Модули получают имя из manifest.name,
# поэтому их подписки не смешиваются с подписками платформы.
PLATFORM_OWNER = "<platform>"


def namespace_of(name):
    """Префикс события до первой точки."""
    return str(name or "").partition(".")[0]


def is_namespaced(name):
    """Есть ли у имени непустой namespace и само имя."""
    prefix, separator, rest = str(name or "").partition(".")
    return bool(prefix and separator and rest)


class Events:
    # -------------------------------------------------- жизненный цикл
    APP_STARTED = "app.started"
    TREE_READY = "ui.tree_ready"
    # Опубликован лес для нового снимка VFS (payload — SnapshotChanged).
    # Смена overlay без публикации леса публикует PROJECT_CHANGED, а не
    # это событие; детали — ADR-15.
    SNAPSHOT_CHANGED = "ui.snapshot_changed"
    # Файл действительно открыт: виджет размещён и стал текущим (payload — путь).
    FILE_OPENED = "ui.file_opened"
    # Открытие не состоялось (payload — путь): отложенная навигация сбрасывается.
    FILE_OPEN_FAILED = "ui.file_open_failed"
    CLEAN_RUN_STARTED = "base.clean_run_started"
    CLEAN_RUN_FINISHED = "base.clean_run_finished"
    ERROR = "app.error"
    # Черновики проекта записаны в каталог проекта (payload — SavedDraft).
    PROJECT_SAVED = "ui.project_saved"
    # Активный проект создан, загружен или удалён.
    PROJECT_CHANGED = "ui.project_changed"
    # Изменены настройки активного проекта (payload — {key: value}).
    PROJECT_SETTINGS_CHANGED = "ui.project_settings_changed"
    # Изменился живой resolved-документ (payload — ResolvedDocumentChange).
    RESOLVED_DOCUMENT_CHANGED = "ui.resolved_document_changed"
    # Запрошено открытие карточки предмета по секции (payload — ItemCardRequest).
    ITEM_CARD_REQUESTED = "ui.item_card_requested"

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
