# -*- coding: utf-8 -*-
"""
PlatformApi — сервисы UI-платформы и точка регистраций для SDK.

Именно этот объект модуль видит как ctx.ui.
"""

from pathlib import Path
import traceback

from PyQt5.QtWidgets import QApplication, QFileDialog, QMessageBox

from sdk.features.projects import AssetDraftInfo, ProjectChanged
from sdk.contracts import (
    AppPaths,
    FileOpened,
    FileOpenFailed,
    SnapshotChanged,
    TreeReady,
)
from sdk.contracts.events import Events, PLATFORM_OWNER
from sdk.contracts.forest import unwrap_forest

from app.paths import PathsAdapter
from app.projects import (
    ProjectAssetRegistry,
    ProjectFileReaderRegistry,
    ProjectService,
    ProjectSettingsRegistry,
)
from app.workspace import SnapshotService, WorkspaceSession

from .file_open_router import FileOpenRouter
from .input_bindings import MouseBindingManager, ShortcutManager
from .module_windows import ModuleWindowManager
from .platform.project_controller import ProjectControllerMixin
from .platform.registration import RegistrationHub
from .platform.resolved import ResolvedDocumentMixin


def _coerce_paths(paths):
    """Привести пути композиционного корня к контракту ``ctx.paths``."""
    if isinstance(paths, PathsAdapter):
        return paths
    if paths is None:
        base = Path.cwd()
        return PathsAdapter(
            AppPaths(
                app_dir=base,
                generated_dir=base / "generated",
                projects_dir=base / "projects",
            )
        )

    app_dir = Path(getattr(paths, "app_dir", Path.cwd()))
    generated_dir = Path(
        getattr(paths, "generated_dir", app_dir / "generated")
    )
    projects_dir = getattr(paths, "projects_dir", None)
    if projects_dir is None:
        projects_dir = app_dir.parent / "projects"
    return PathsAdapter(
        AppPaths(
            app_dir=app_dir,
            generated_dir=generated_dir,
            projects_dir=Path(projects_dir),
        )
    )


class PlatformApi(ProjectControllerMixin, ResolvedDocumentMixin):
    def __init__(
        self,
        events,
        commands,
        tasks,
        services,
        paths,
        hints=None,
        snapshot_service=None,
    ):
        self.events = events
        self.commands = commands
        self.tasks = tasks
        self.services = services
        self.paths = _coerce_paths(paths)
        self.hints = hints

        self.router = FileOpenRouter()
        self._window_manager = ModuleWindowManager()
        self._shortcut_manager = ShortcutManager(self.trigger_action)
        self._mouse_manager = MouseBindingManager(self.trigger_action)

        self.window = None
        self.registrations = RegistrationHub()
        self._log_buffer = []

        projects_dir = self.paths.projects_dir
        self.asset_types = ProjectAssetRegistry()
        self.project_setting_registry = ProjectSettingsRegistry()
        self.file_readers = ProjectFileReaderRegistry()
        self.project = ProjectService(
            projects_dir,
            asset_registry=self.asset_types,
            file_readers=self.file_readers,
        )
        self.workspace = WorkspaceSession(
            services,
            snapshot_service=snapshot_service or SnapshotService(),
        )
        # Корни, которые нужно пересобрать после последней точечной
        # инвалидации. ``None`` — сборка всего леса.
        self._project_prebuild_roots = None
        # Был ли уже построен предпросмотр патча для активного проекта:
        # после него пункт меню называется «Пересобрать патч».
        self._patch_preview_ready = False
        # На старте действует обычная тема. ``False`` (а не ``None``)
        # избавляет первую правку от повторной установки stylesheet.
        self._override_theme_enabled = False

        # Отложенная навигация: ключ — id запроса открытия.
        # Несколько запросов на один путь не перетирают друг друга:
        # путь -> очередь id, а событие FILE_OPENED/FAILED несёт request_id.
        self._file_request_seq = 0
        self._pending_file_actions = {}
        self._pending_file_request_ids = {}
        self._active_file_request_ids = []

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


    # -------------------------------------------------- запросы открытия
    def _path_key(self, path):
        """Нормализовать путь для сопоставления запроса и события."""
        return self.paths.key(path)

    def _next_file_request_id(self):
        self._file_request_seq += 1
        return self._file_request_seq

    def _queue_file_action(self, path, kind, value):
        """Запомнить навигацию и вернуть id запроса открытия."""
        request_id = self._next_file_request_id()
        key = self._path_key(path)
        self._pending_file_actions[request_id] = (key, kind, value)
        self._pending_file_request_ids.setdefault(key, []).append(request_id)
        return request_id

    def _discard_file_request(self, request_id):
        record = self._pending_file_actions.pop(int(request_id or 0), None)
        if record is None:
            return False
        key = record[0]
        ids = self._pending_file_request_ids.get(key)
        if ids:
            try:
                ids.remove(int(request_id))
            except ValueError:
                pass
            if not ids:
                self._pending_file_request_ids.pop(key, None)
        return True

    def _discard_file_requests_for_path(self, path):
        key = self._path_key(path)
        ids = self._pending_file_request_ids.pop(key, ())
        for request_id in ids:
            self._pending_file_actions.pop(request_id, None)
        return len(ids)

    def _oldest_file_request_id(self, path):
        key = self._path_key(path)
        ids = self._pending_file_request_ids.get(key) or ()
        return int(ids[0]) if ids else 0

    def _active_file_request_id(self):
        if not self._active_file_request_ids:
            return 0
        return int(self._active_file_request_ids[-1] or 0)

    def current_file_request_id(self):
        """id текущего запроса открытия (0 вне обработчика)."""
        return self._active_file_request_id()

    def _resolve_file_request_id(self, path, request_id=None, claim=False):
        """Выбрать request_id для события открытия.

        ``request_id`` передан явно — используем его. Иначе берём активный
        запрос (синхронный обработчик) или, если ``claim``, самый старый
        ожидающий запрос для этого пути (асинхронный обработчик).
        """
        if request_id is not None:
            return int(request_id or 0)
        active = self._active_file_request_id()
        if active:
            return active
        if claim:
            return self._oldest_file_request_id(path)
        return 0

    def _run_file_request(self, request_id, opener):
        """Выполнить открытие, привязав к нему request_id."""
        self._active_file_request_ids.append(int(request_id or 0))
        try:
            return bool(opener())
        finally:
            if self._active_file_request_ids:
                self._active_file_request_ids.pop()

    def _pop_oldest_file_request(self, path):
        key = self._path_key(path)
        ids = self._pending_file_request_ids.get(key)
        if not ids:
            return None
        while ids:
            request_id = int(ids.pop(0))
            record = self._pending_file_actions.pop(request_id, None)
            if record is None:
                continue
            if not ids:
                self._pending_file_request_ids.pop(key, None)
            return request_id, record[1], record[2]
        self._pending_file_request_ids.pop(key, None)
        return None

    def _clear_pending_file_actions(self):
        self._pending_file_actions.clear()
        self._pending_file_request_ids.clear()

    # Совместимые алиасы: раньше лес и виртуальные пути жили прямо в
    # PlatformApi. Теперь их владелец — WorkspaceSession.
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
    def register_menu(self, menu_title, label, callback, shortcut=None,
                      action=None, owner=None):
        return self.registrations.register_menu(
            menu_title,
            label,
            callback,
            shortcut,
            action,
            owner=owner,
        )

    def register_tree_context_action(self, label, handler, predicate=None,
                                     owner=None):
        """Добавить действие в контекстное меню дерева.

        ``predicate(path)`` — необязательный фильтр: действие показывается
        только для путей, для которых он вернул truthy.
        """
        return self.registrations.register_tree_context_action(
            label,
            handler,
            predicate,
            owner=owner,
        )

    def register_file_action(self, label, handler, predicate=None,
                             owner=None):
        """Действие в контекстном меню вкладки файла.

        ``handler(path)`` вызывается платформой; имя модуля и его команды
        здесь не участвуют.
        """
        return self.registrations.register_file_action(
            label,
            handler,
            predicate,
            owner=owner,
        )

    @property
    def file_actions(self):
        return self.registrations.file_actions

    def add_registration_listener(self, listener):
        """Уведомлять UI об изменении модульных меню."""
        return self.registrations.add_listener(listener)

    def register_shortcut(self, key, callback, scope="global", action=None,
                          owner=None):
        return self._shortcut_manager.register(
            key,
            callback,
            scope=scope,
            action=action,
            owner=owner,
        )

    def register_mouse_binding(
        self,
        button,
        modifiers,
        callback,
        scope="global",
        event="press",
        action=None,
        owner=None,
    ):
        return self._mouse_manager.register(
            button,
            modifiers,
            callback,
            scope=scope,
            event=event,
            action=action,
            owner=owner,
        )

    def register_action_handler(self, action, handler, priority=0, owner=None):
        return self.events.subscribe(
            action,
            handler,
            priority,
            owner=PLATFORM_OWNER if owner is None else owner,
        )

    def dispatch_action(self, action, payload=None):
        return self.events.dispatch(action, payload)

    def _safe_callback(self, label, callback):
        if not callable(callback):
            self.log("Действие %s: callback не задан" % label)
            return False
        try:
            result = callback()
        except Exception:
            self.log(
                "Ошибка действия %s:\n%s"
                % (label, traceback.format_exc())
            )
            self.set_status("Ошибка действия: %s" % label, 5000)
            return False
        return bool(result)

    def trigger_action(self, action, callback, payload=None, fallback=None):
        """Выполнить UI-действие с возможностью перехвата.

        Сначала рассылается конкретное ``action``, затем общее действие
        ``fallback`` (например, ``ui.shortcut``). Если любое из них
        перехвачено, ``callback`` не вызывается. Ошибка callback'а
        логируется и не выходит в Qt event loop.
        """
        if action and self.events.dispatch(action, payload):
            return True
        if fallback and self.events.dispatch(fallback, payload):
            return True
        return self._safe_callback(action or "<callback>", callback)

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

        if not paths:
            reason = "нет путей"
        elif not roots:
            reason = "путь не связан с корнем дерева"
        else:
            reason = "сервис не поддерживает точечную инвалидацию"
        self.log("Проект: полная инвалидация кэша (%s)" % reason)
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
        return self.workspace.forest_ref

    def publish_tree_ready(self, forest):
        """Сохранить лес у платформы и разослать лёгкий TreeReady."""
        raw = unwrap_forest(forest)
        previous_snapshot = self.workspace.snapshot_id
        self.workspace.set_forest(forest)
        current_snapshot = self.workspace.snapshot_id
        forest_snapshot = (
            self.workspace.forest_snapshot_id
            or str(getattr(raw, "cache_id", "") or "")
        )
        if self.workspace.is_stale:
            self.log(
                "Дерево собрано для overlay %s, актуален %s: "
                "требуется пересборка VFS"
                % (
                    getattr(raw, "overlay_revision", "") or "—",
                    getattr(self.workspace.snapshot, "overlay_revision", "")
                    or "—",
                )
            )
        summary = None
        try:
            summary = self.services.tree_summary(raw)
        except Exception:
            pass
        payload = TreeReady(
            cache_id=forest_snapshot,
            generation=int(self.workspace.generation),
            summary=summary,
        )
        if current_snapshot and current_snapshot != previous_snapshot:
            self.events.publish(
                Events.SNAPSHOT_CHANGED,
                SnapshotChanged(
                    snapshot_id=current_snapshot,
                    generation=int(self.workspace.generation),
                ),
            )
        self.events.publish(Events.TREE_READY, payload)
        return payload

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
            from sdk.ui.hints import NullHintsHandle

            return NullHintsHandle()
        return self.hints.attach(
            widget,
            target=target,
            providers=providers,
            enabled=enabled,
        )

    def request_window(self, window_id):
        window_id = str(window_id or "")
        if self.dispatch_action(Events.UI_WINDOW_REQUESTED, window_id):
            return True
        # Модуль мог уже показать окно, но не подписаться на запрос:
        # платформа сама поднимает известное окно по ключу.
        return self._window_manager.focus(window_id)

    def request_tab(self, tab_id):
        tab_id = str(tab_id or "")
        if self.dispatch_action(Events.UI_TAB_REQUESTED, tab_id):
            return True
        if self.window is None:
            return False
        editor = getattr(self.window, "editor", None)
        setter = getattr(editor, "set_current_tab", None)
        if not callable(setter):
            return False
        try:
            return bool(setter(tab_id))
        except Exception:
            self.log(
                "request_tab(%s): ошибка переключения вкладки:\n%s"
                % (tab_id, traceback.format_exc())
            )
            return False

    def request_file_open(self, path):
        path = Path(path)
        if self.dispatch_action(Events.UI_FILE_OPEN_REQUESTED, path):
            return True
        try:
            return bool(self.commands.execute("ui.open_file", path))
        except Exception:
            self.log(
                "request_file_open(%s): ошибка открытия:\n%s"
                % (path, traceback.format_exc())
            )
            return False

    # -------------------------------------------------- команды платформы
    def request_open_file(self, path):
        # Явное открытие без навигации отменяет ожидавшие её запросы.
        self._discard_file_requests_for_path(path)
        ok = self._run_file_request(
            0,
            lambda: self.router.open(path, self),
        )
        if not ok:
            self.notify_file_open_failed(
                path,
                "Не найден обработчик для открытия файла",
                request_id=0,
            )
            self.show_error(
                "Не найден обработчик для открытия файла: %s" % path
            )
        return ok

    # -------------------------------------------------- регистрации (SDK)
    def register_open_handler(self, handler, context=None, owner=None):
        """Модуль регистрирует своего обработчика открытия файла."""
        return self.router.register(handler, context=context, owner=owner)

    # -------------------------------------------------- окна модулей
    def show_module_window(self, key, window, owner=None):
        """Показать окно, созданное модулем."""
        self._window_manager.show(key, window, owner=owner)
        self.events.publish(Events.UI_WINDOW_SHOWN, key)

    def close_module_window(self, key):
        """Закрыть окно модуля по ключу."""
        self._window_manager.close(key)
        self.events.publish(Events.UI_WINDOW_CLOSED, key)

    def close_module_windows(self, owner=None):
        """Закрыть окна модулей (все или конкретного владельца)."""
        if owner is None:
            self._window_manager.close_all()
        else:
            self._window_manager.close_owner(owner)

    # -------------------------------------------------- вкладки редактора
    def open_editor_tab(self, tab_id, title, widget, owner=None):
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

    def close_module_tabs(self, owner):
        """Закрыть все вкладки, принадлежащие модулю."""
        if self.window is None:
            return 0
        closer = getattr(self.window.editor, "close_owner_tabs", None)
        if not callable(closer):
            return 0
        return closer(owner)

    # -------------------------------------------------- ui-сервисы
    def show_text(self, text, label, readonly=True, source_path=None,
                  line_number=None, syntax=True, notice=None,
                  request_id=None, tab_id=None):
        if self.window is not None:
            # Показ текста может завершать отложенный запрос открытия:
            # событие FILE_OPENED должно нести его request_id.
            resolved_id = self._resolve_file_request_id(
                source_path,
                request_id,
                claim=source_path is not None,
            )
            self.window.editor.show_text(
                text,
                label,
                readonly=readonly,
                source_path=source_path,
                line_number=line_number,
                syntax=syntax,
                notice=notice,
                request_id=resolved_id,
                tab_id=tab_id,
            )
        else:
            self._log_buffer.append("[show_text до окна] %s" % label)

    def request_open_file_raw(self, path):
        self._discard_file_requests_for_path(path)
        ok = self._run_file_request(
            0,
            lambda: self.router.open_default(path, self),
        )
        if not ok:
            self.notify_file_open_failed(
                path,
                "Нет обработчика по умолчанию",
                request_id=0,
            )
            self.show_error("Нет обработчика по умолчанию для: %s" % path)
        return ok

    def notify_file_open_failed(self, path, reason="", request_id=None):
        """Опубликовать факт неудачного открытия от имени платформы."""
        resolved_id = self._resolve_file_request_id(
            path,
            request_id,
            claim=request_id is None,
        )
        self.events.publish(
            Events.FILE_OPEN_FAILED,
            FileOpenFailed(
                path=Path(path),
                request_id=resolved_id,
                reason=str(reason or ""),
            ),
        )

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

    def _request_open_with_action(
        self,
        path,
        kind,
        value,
        opener,
        missing_message,
    ):
        """Запросить открытие с отложенной навигацией.

        У каждого запроса свой id: два запроса на один путь не затирают
        друг друга и получают ровно своё событие открытия.
        """
        path = Path(path)
        request_id = self._queue_file_action(path, kind, value)
        ok = self._run_file_request(request_id, opener)
        if not ok:
            self._discard_file_request(request_id)
            self.notify_file_open_failed(
                path,
                missing_message,
                request_id=request_id,
            )
            self.show_error("%s: %s" % (missing_message, path))
        return ok

    def request_open_file_at_line(self, path, line_number: int):
        """Открыть файл и перейти к указанной строке.

        Навигация применяется не сразу: файл может открываться асинхронно
        (резолв .ltx). Запрос ждёт события :attr:`Events.FILE_OPENED`
        с собственным ``request_id``.
        """
        return self._request_open_with_action(
            path,
            "line",
            int(line_number),
            lambda: self.router.open(path, self),
            "Не найден обработчик для открытия файла",
        )

    def request_open_file_with_search(self, path, search_text):
        """Открыть файл и перейти к первому вхождению ``search_text``.

        Навигация откладывается до факта открытия именно этого пути:
        для резолва .ltx виджет создаётся позже, после фоновой сборки.
        """
        return self._request_open_with_action(
            path,
            "search",
            search_text,
            lambda: self.router.open(path, self),
            "Не найден обработчик для открытия файла",
        )

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
        if path is None or not tab_id or self.window is None:
            return

        key = self._path_key(path)
        request_id = int(opened.request_id or 0)
        pending = None

        if request_id:
            record = self._pending_file_actions.get(request_id)
            if record is None:
                # Запрос уже отменён или устарел: чужую навигацию не
                # подставляем даже при совпадении пути.
                return
            if record[0] != key:
                self.log(
                    "[open] request_id=%s относится к другому пути: %s"
                    % (request_id, path)
                )
                return
            self._discard_file_request(request_id)
            pending = (record[1], record[2])
        else:
            oldest = self._pop_oldest_file_request(path)
            if oldest is None:
                return
            request_id, kind, value = oldest
            pending = (kind, value)

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
        request_id = int(failed.request_id or 0)
        if request_id:
            record = self._pending_file_actions.get(request_id)
            if record is not None and record[0] != self._path_key(failed.path):
                self.log(
                    "[open] request_id=%s относится к другому пути: %s"
                    % (request_id, failed.path)
                )
                return
            removed = self._discard_file_request(request_id)
        else:
            removed = self._pop_oldest_file_request(failed.path) is not None
        if removed:
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
        return self._request_open_with_action(
            path,
            "line",
            int(line_number),
            lambda: self.router.open_default(path, self),
            "Нет обработчика по умолчанию",
        )

    def request_open_file_raw_with_search(self, path, search_text):
        """Открыть файл как сырой текст и перейти к строке поиска."""
        return self._request_open_with_action(
            path,
            "search",
            search_text,
            lambda: self.router.open_default(path, self),
            "Нет обработчика по умолчанию",
        )
