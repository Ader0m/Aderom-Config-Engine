# -*- coding: utf-8 -*-
"""
PlatformApi — сервисы UI-платформы и точка регистраций для SDK.

Именно этот объект модуль видит как ctx.ui.
"""

from pathlib import Path

from PyQt5.QtWidgets import QApplication, QFileDialog, QMessageBox

from sdk.api import (
    AssetDraftInfo,
    FileOpened,
    FileOpenFailed,
)
from sdk.events import Events

from app.projects import (
    ProjectAssetRegistry,
    ProjectFileReaderRegistry,
    ProjectService,
    ProjectSettingsRegistry,
)
from app.workspace import WorkspaceService

from .file_open_router import FileOpenRouter
from .input_bindings import MouseBindingManager, ShortcutManager
from .module_windows import ModuleWindowManager
from .platform.project_controller import ProjectControllerMixin
from .platform.registration import RegistrationHub
from .platform.resolved import ResolvedDocumentMixin


def _path_key(path):
    """Нормализовать путь для сопоставления запроса открытия и события."""
    from sdk.paths import path_key

    return path_key(path)


class PlatformApi(ProjectControllerMixin, ResolvedDocumentMixin):
    def __init__(self, events, commands, tasks, services, paths, hints=None):
        self.events = events
        self.commands = commands
        self.tasks = tasks
        self.services = services
        self.paths = paths
        self.hints = hints

        self.router = FileOpenRouter()
        self._window_manager = ModuleWindowManager()
        self._shortcut_manager = ShortcutManager(self.trigger_action)
        self._mouse_manager = MouseBindingManager(self.trigger_action)

        self.window = None
        self.registrations = RegistrationHub()
        self._log_buffer = []

        projects_dir = getattr(paths, "projects_dir", None)
        if projects_dir is None:
            # Проекты лежат рядом с каталогом src, а не внутри него.
            projects_dir = (
                Path(getattr(paths, "app_dir", ".")).resolve().parent
                / "projects"
            )
        self.asset_types = ProjectAssetRegistry()
        self.project_setting_registry = ProjectSettingsRegistry()
        self.file_readers = ProjectFileReaderRegistry()
        self.project = ProjectService(
            projects_dir,
            asset_registry=self.asset_types,
            file_readers=self.file_readers,
        )
        self.workspace = WorkspaceService(services)
        # Корни, которые нужно пересобрать после последней точечной
        # инвалидации. ``None`` — сборка всего леса.
        self._project_prebuild_roots = None
        # На старте действует обычная тема. ``False`` (а не ``None``)
        # избавляет первую правку от повторной установки stylesheet.
        self._override_theme_enabled = False

        # Отложенная навигация: ключ — нормализованный путь файла.
        # Запрос живёт до события FILE_OPENED ровно для этого пути.
        self._pending_file_actions = {}

        # Живые resolved-документы: один владелец текста на tab_id.
        self._resolved_documents = {}

        # Очереди фоновых правок resolved-документов по tab_id.
        self._resolved_edit_queues = {}
        self._resolved_edit_active = set()

        # Фоновые записи текстовых черновиков: не более одной на файл.
        # ``_text_save_pending`` хранит последний текст, пришедший во
        # время уже выполняющейся записи.
        self._text_save_jobs = {}
        self._text_save_pending = {}

        self._file_opened_sub = events.subscribe(
            Events.FILE_OPENED,
            self._on_file_opened,
        )
        self._file_open_failed_sub = events.subscribe(
            Events.FILE_OPEN_FAILED,
            self._on_file_open_failed,
        )
        self._tree_ready_sub = events.subscribe(
            Events.TREE_READY,
            self._on_tree_ready,
        )
        self._tab_closed_sub = events.subscribe(
            Events.UI_TAB_CLOSED,
            self._on_tab_closed,
        )
        self._project_changed_sub = events.subscribe(
            Events.PROJECT_CHANGED,
            self._on_project_changed,
        )

        commands.register("ui.open_file", self.request_open_file)
        commands.register("ui.open_file_raw", self.request_open_file_raw)
        commands.register("ui.editor_undo", self.request_editor_undo)
        commands.register("ui.editor_redo", self.request_editor_redo)
        commands.register("ui.editor_find", self.request_editor_find)
        commands.register("ui.open_file_at_line", self.request_open_file_at_line)
        commands.register(
            "ui.open_file_raw_at_line",
            self.request_open_file_raw_at_line,
        )
        commands.register("ui.open_file_with_search", self.request_open_file_with_search)


    # Совместимые алиасы: раньше лес и виртуальные пути жили прямо в
    # PlatformApi. Теперь их владелец — WorkspaceService.
    @property
    def _forest(self):
        return self.workspace.forest

    @_forest.setter
    def _forest(self, value):
        self.workspace.set_forest(value)

    @property
    def _virtual_paths(self):
        return self.workspace.virtual_paths

    # -------------------------------------------------- регистрации (SDK)
    def register_menu(self, menu_title, label, callback, shortcut=None, action=None):
        self.registrations.register_menu(
            menu_title,
            label,
            callback,
            shortcut,
            action,
        )

    def register_tree_context_action(self, label, handler, predicate=None):
        """Добавить действие в контекстное меню дерева.

        ``predicate(path)`` — необязательный фильтр: действие показывается
        только для путей, для которых он вернул truthy.
        """
        self.registrations.register_tree_context_action(
            label,
            handler,
            predicate,
        )

    def register_file_action(self, label, handler, predicate=None):
        """Действие в контекстном меню вкладки файла.

        ``handler(path)`` вызывается платформой; имя модуля и его команды
        здесь не участвуют.
        """
        self.registrations.register_file_action(
            label,
            handler,
            predicate,
        )

    @property
    def file_actions(self):
        return self.registrations.file_actions

    def register_shortcut(self, key, callback, scope="global", action=None):
        self._shortcut_manager.register(key, callback, scope=scope, action=action)

    def register_mouse_binding(
        self,
        button,
        modifiers,
        callback,
        scope="global",
        event="press",
        action=None,
    ):
        self._mouse_manager.register(
            button,
            modifiers,
            callback,
            scope=scope,
            event=event,
            action=action,
        )

    def register_action_handler(self, action, handler, priority=0):
        return self.events.subscribe(action, handler, priority)

    def dispatch_action(self, action, payload=None):
        return self.events.dispatch(action, payload)

    def trigger_action(self, action, callback, payload=None, fallback=None):
        """Выполнить UI-действие с возможностью перехвата.

        Сначала рассылается конкретное ``action``, затем общее действие
        ``fallback`` (например, ``ui.shortcut``). Если любое из них
        перехвачено, ``callback`` не вызывается.
        """
        if action and self.events.dispatch(action, payload):
            return True
        if fallback and self.events.dispatch(fallback, payload):
            return True
        result = callback()
        return bool(result)

    @property
    def menu_registrations(self):
        return self.registrations.menu_registrations

    @property
    def context_actions(self):
        return self.registrations.context_actions

    # -------------------------------------------------- привязка окна
    def bind_window(self, window):
        self.window = window
        self._override_theme_enabled = None
        self._window_manager.bind_main_window(window)
        self._shortcut_manager.attach(window)
        self._mouse_manager.attach(window)

        editor = getattr(window, "editor", None)
        if editor is not None:
            editor.document_changed.connect(self.on_document_changed)
            editor.save_requested.connect(self.save_current_document)
            editor.lexer_status.connect(self.on_lexer_status)
            editor.set_editable(self.can_edit_documents())

        buf, self._log_buffer = self._log_buffer, []
        for m in buf:
            window.log(m)

        self._update_project_status()

    # -------------------------------------------------- проект
    def _on_tree_ready(self, forest):
        self._forest = forest

    def _invalidate_resolver_cache(self):
        """Сбросить кеш и пометить предсборку устаревшей."""
        # Полная инвалидация отменяет и точечный набор корней: иначе
        # следующая предсборка соберёт только старый «затронутый» корень
        # и оставит остальной кеш пустым.
        self._project_prebuild_roots = None
        invalidate = getattr(
            self.services,
            "invalidate_resolver_cache",
            None,
        )
        if callable(invalidate):
            invalidate()
            return
        invalidate = getattr(self.services, "invalidate_cache", None)
        if callable(invalidate):
            invalidate()
            return
        resolver = getattr(self.services, "resolver", None)
        if resolver is not None and hasattr(resolver, "invalidate_cache"):
            resolver.invalidate_cache()

    def _invalidate_paths(self, paths):
        """Инвалидировать только корни, в поддеревья которых входят пути.

        Возвращает ``True``, если удалось определить затронутые корни.
        Если путь не связан с деревом (или сервис не умеет точечную
        инвалидацию), выполняется полная инвалидация.
        """
        paths = [Path(path) for path in (paths or ()) if path is not None]
        roots = self.workspace.roots_for(paths)
        invalidate = getattr(self.services, "invalidate_paths", None)
        if roots and callable(invalidate):
            invalidate(roots)
            self._project_prebuild_roots = roots
            self.log("Проект: инвалидированы корни: %d" % len(roots))
            return True

        self._invalidate_resolver_cache()
        return False


    def has_forest(self):
        return self._forest is not None

    def can_edit_documents(self):
        return self.project.is_active

    # -------------------------------------------------- resolved-документы
    def on_lexer_status(self, message):
        """Обновляемая строка покраски файла в диагностике."""
        self.log_status("ui.lexer", message)
        if message.endswith("готово") or message.endswith("…"):
            self.set_status(message, 5000)

    def log(self, msg):
        if self.window is not None:
            self.window.log(msg)
        else:
            self._log_buffer.append(str(msg))

    def log_status(self, key, msg):
        """Обновить статусную строку диагностики (прогресс, кэш и т.п.)."""
        if self.window is not None:
            self.window.log_status(str(key), str(msg))
        else:
            self._log_buffer.append(str(msg))

    def set_status(self, msg, timeout=0):
        if self.window is not None:
            self.window.statusBar().showMessage(str(msg), timeout)

    def show_error(self, text):
        if self.window is not None:
            try:
                QMessageBox.critical(self.window, "Ошибка", str(text))
                return
            except TypeError:
                pass
        self.log("Ошибка: %s" % text)

    def ask_directory(self, initial=""):
        d = QFileDialog.getExistingDirectory(
            self.window,
            "Выбрать папку с конфигами",
            initial or "",
            QFileDialog.ShowDirsOnly,
        )
        return d or None

    def clipboard_set(self, text):
        QApplication.clipboard().setText(str(text))

    def theme_color(self, name, fallback=None):
        from .theme import PALETTE

        return getattr(PALETTE, str(name), fallback)

    def workspace_forest(self):
        return self.workspace.forest

    def workspace_generation(self):
        return self.workspace.generation

    def attach_text_hints(
        self,
        widget,
        target=None,
        providers=None,
        enabled=True,
    ):
        if self.hints is None:
            from sdk.hints import NullHintsHandle

            return NullHintsHandle()
        return self.hints.attach(
            widget,
            target=target,
            providers=providers,
            enabled=enabled,
        )

    def request_window(self, window_id):
        return self.dispatch_action(
            Events.UI_WINDOW_REQUESTED,
            str(window_id or ""),
        )

    def request_tab(self, tab_id):
        return self.dispatch_action(
            Events.UI_TAB_REQUESTED,
            str(tab_id or ""),
        )

    def request_file_open(self, path):
        if self.dispatch_action(Events.UI_FILE_OPEN_REQUESTED, Path(path)):
            return True
        self.commands.execute("ui.open_file", Path(path))
        return True

    # -------------------------------------------------- команды платформы
    def request_open_file(self, path):
        self._pending_file_actions.pop(_path_key(path), None)
        ok = self.router.open(path, self)
        if not ok:
            self.show_error("Не найден обработчик для открытия файла: %s" % path)
        return ok

    # -------------------------------------------------- регистрации (SDK)
    def register_open_handler(self, handler, context=None):
        """Модуль регистрирует своего обработчика открытия файла."""
        self.router.register(handler, context=context)

    # -------------------------------------------------- окна модулей
    def show_module_window(self, key, window):
        """Показать окно, созданное модулем."""
        self._window_manager.show(key, window)
        self.events.publish(Events.UI_WINDOW_SHOWN, key)

    def close_module_window(self, key):
        """Закрыть окно модуля по ключу."""
        self._window_manager.close(key)
        self.events.publish(Events.UI_WINDOW_CLOSED, key)

    def close_module_windows(self):
        """Закрыть все окна, открытые модулями."""
        self._window_manager.close_all()

    # -------------------------------------------------- вкладки редактора
    def open_editor_tab(self, tab_id, title, widget):
        """Открыть вкладку, созданную модулем."""
        if self.window is None:
            self.log("[open_editor_tab до окна] %s" % tab_id)
            return
        self.window.editor.open_tab(tab_id, title, widget)

    def close_editor_tab(self, tab_id):
        """Закрыть вкладку модуля."""
        if self.window is None:
            return
        self.window.editor.close_tab(tab_id)

    # -------------------------------------------------- ui-сервисы
    def show_text(self, text, label, readonly=True, source_path=None,
                  line_number=None, syntax=True, notice=None):
        # Новое явное отображение отменяет старую отложенную навигацию
        # для этого же файла.
        if source_path is not None:
            self._pending_file_actions.pop(_path_key(source_path), None)
        if self.window is not None:
            self.window.editor.show_text(
                text,
                label,
                readonly=readonly,
                source_path=source_path,
                line_number=line_number,
                syntax=syntax,
                notice=notice,
            )
        else:
            self._log_buffer.append("[show_text до окна] %s" % label)

    def request_open_file_raw(self, path):
        self._pending_file_actions.pop(_path_key(path), None)
        ok = self.router.open_default(path, self)
        if not ok:
            self.show_error("Нет обработчика по умолчанию для: %s" % path)
        return ok

    def request_editor_undo(self):
        if self.window is not None:
            self.window.editor.undo()

    def request_editor_redo(self):
        if self.window is not None:
            self.window.editor.redo()

    def request_editor_find(self):
        if self.window is not None:
            self.window.editor.show_find()

    def sort_file_tree_by_files_desc(self):
        """Платформенное действие над деревом, без знания SDK о виджете."""
        if self.window is None:
            return False
        tree = getattr(self.window, "file_tree", None)
        fn = getattr(tree, "sort_by_files_desc", None)
        if callable(fn):
            fn()
            return True
        return False

    def ask_text(self, prompt: str, initial: str = "") -> str:
        """Запросить текст у пользователя через диалог."""
        from PyQt5.QtWidgets import QInputDialog
        
        text, ok = QInputDialog.getText(
            self.window,
            "Ввод",
            prompt,
            text=initial,
        )
        return text if ok and text else ""

    def request_open_file_at_line(self, path, line_number: int):
        """Открыть файл и перейти к указанной строке.

        Навигация применяется не сразу: файл может открываться асинхронно
        (резолв .ltx). Запрос ждёт события :attr:`Events.FILE_OPENED` для
        этого же пути.
        """
        key = _path_key(path)
        self._pending_file_actions.pop(key, None)
        self._pending_file_actions[key] = ("line", int(line_number))

        ok = self.router.open(path, self)
        if not ok:
            self._pending_file_actions.pop(key, None)
            self.show_error("Не найден обработчик для открытия файла: %s" % path)
        return ok

    def request_open_file_with_search(self, path, search_text):
        """Открыть файл и перейти к первому вхождению ``search_text``.

        Навигация откладывается до факта открытия именно этого пути:
        для резолва .ltx виджет создаётся позже, после фоновой сборки.
        """
        key = _path_key(path)
        self._pending_file_actions.pop(key, None)
        self._pending_file_actions[key] = ("search", search_text)

        ok = self.router.open(path, self)
        if not ok:
            self._pending_file_actions.pop(key, None)
            self.show_error("Не найден обработчик для: %s" % path)
        return ok

    # -------------------------------------------------- факты открытия
    def _on_file_opened(self, opened):
        """Событие FILE_OPENED: навигация для открытого файла."""
        if not isinstance(opened, FileOpened):
            self.log(
                "[open] FILE_OPENED должен содержать FileOpened, получено: %r"
                % type(opened).__name__
            )
            return
        path = opened.path
        tab_id = str(opened.tab_id or "")
        if path is None or not tab_id:
            return

        pending = self._pending_file_actions.pop(_path_key(path), None)
        if pending is None or self.window is None:
            return

        kind, value = pending
        widget = self.window.editor.get_tab(tab_id)

        if kind == "search":
            if widget is not None and hasattr(widget, "find_first"):
                if value and not widget.find_first(value):
                    self.set_status(
                        "Владелец открыт, но строка не найдена: %s" % value,
                        8000,
                    )
            else:
                self.log(
                    "[open_file_with_search] активная вкладка не поддерживает "
                    "поиск: %s" % type(widget).__name__
                )
        elif kind == "line":
            if widget is not None and hasattr(widget, "goto_line"):
                widget.goto_line(value)
            else:
                self.log(
                    "[open_file_at_line] активная вкладка не поддерживает "
                    "переход к строке: %s" % type(widget).__name__
                )

    def _on_file_open_failed(self, failed):
        """Файл не открылся: отложенная навигация больше не нужна."""
        if not isinstance(failed, FileOpenFailed):
            self.log(
                "[open] FILE_OPEN_FAILED должен содержать FileOpenFailed, "
                "получено: %r" % type(failed).__name__
            )
            return
        if self._pending_file_actions.pop(
            _path_key(failed.path),
            None,
        ) is not None:
            self.log(
                "[open] навигация отменена, файл не открыт: %s"
                % failed.path
            )

    def _log_slow_view(self, kind, title, content_elapsed, tab_elapsed):
        """Диагностика: показать, сколько заняла отрисовка вкладки."""
        total = content_elapsed + tab_elapsed
        if total >= 0.5:
            self.log(
                "[%s] отрисовка %.2f s (контент %.2f, вкладка %.2f): %s"
                % (kind, total, content_elapsed, tab_elapsed, title)
            )

    def request_open_file_raw_at_line(self, path, line_number: int):
        """Открыть файл сырым текстом и перейти к указанной строке.

        В отличие от :meth:`request_open_file_at_line`, обходит
        зарегистрированные обработчики (например, XML-резолв) и всегда
        открывает исходный файл. Это нужно для перехода к объявлению
        строки локализации: ``line`` относится к исходному XML.
        """
        key = _path_key(path)
        self._pending_file_actions.pop(key, None)
        self._pending_file_actions[key] = ("line", int(line_number))

        ok = self.router.open_default(path, self)
        if not ok:
            self._pending_file_actions.pop(key, None)
            self.show_error("Нет обработчика по умолчанию для: %s" % path)
        return ok

    def request_open_file_raw_with_search(self, path, search_text):
        """Открыть файл как сырой текст и перейти к строке поиска."""
        key = _path_key(path)
        self._pending_file_actions.pop(key, None)
        self._pending_file_actions[key] = ("search", search_text)

        ok = self.router.open_default(path, self)
        if not ok:
            self._pending_file_actions.pop(key, None)
            self.show_error("Нет обработчика по умолчанию для: %s" % path)
        return ok
