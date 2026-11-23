# -*- coding: utf-8 -*-
"""
Главное окно — оболочка рабочей среды (платформа).

Здесь НЕТ бизнес-сценариев.
Поиск по имени файла — платформенная возможность дерева:
запрос данных идёт через api.services (TreeQueryService.search_nodes).
"""

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QAction,
    QDockWidget,
    QLabel,
    QMainWindow,
    QMenu,
    QShortcut,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from sdk.features.items import ITEM_SECTION_MIME
from sdk.contracts.events import Events

from .diagnostics import DiagnosticsLog
from .editor_tabs import EditorTabs
from .file_open_router import TextEditorHandler
from .file_tree import FileTreeWidget
from .platform.feature_flags import PATCH_WRITE_EXPERIMENTAL
from .prebuild_panel import PrebuildPanel
from .search_bar import SearchBar


class MainWindow(QMainWindow):
    def __init__(self, api):
        super().__init__()

        self.api = api
        self.setAcceptDrops(True)

        self.setWindowTitle("LTX Explorer")
        self.resize(1400, 900)

        self._init_ui()
        self._init_menu()
        self._init_statusbar()

        self._tree_ready_sub = self.api.events.subscribe(
            Events.TREE_READY,
            self._on_tree_ready,
        )

        self.api.bind_window(self)

    # -------------------------------------------------- UI
    def _init_ui(self):
        self.splitter = QSplitter(Qt.Horizontal)

        # Дерево + строка поиска в левой панели
        self.file_tree = FileTreeWidget(sort_fn=self.api.services.tree_query.sort_nodes)
        self.file_tree.file_opened.connect(
            lambda p: self.api.commands.execute("ui.open_file", p)
        )
        self.file_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_tree.customContextMenuRequested.connect(self._on_tree_context)

        self.search_bar = SearchBar()
        self.search_bar.set_search_fn(self._search_by_name)
        self.search_bar.matches_changed.connect(self.file_tree.highlight)
        self.search_bar.current_changed.connect(self.file_tree.reveal_path)
        self.search_bar.cleared.connect(self.file_tree.clear_highlight)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        left_layout.addWidget(self.search_bar)
        left_layout.addWidget(self.file_tree, 1)

        # Редактор
        self.editor = EditorTabs(
            events=self.api.events,
            commands=self.api.commands,
            can_edit=self.api.can_edit_documents,
            file_actions=lambda: self.api.file_actions,
            hint_attach=self.api.attach_text_hints,
            text_reader=self.api.document_text,
        )
        self.api.router.set_default(TextEditorHandler(self.editor))

        self.splitter.addWidget(left)
        self.splitter.addWidget(self.editor)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 3)
        # Стартовые пропорции: дерево ~25%, редактор ~75%. Без явных
        # sizes пустой EditorTabs (без вкладок) схлопывается по sizeHint.
        self.splitter.setSizes([350, 1050])

        self.setCentralWidget(self.splitter)

        # Ctrl+F — поиск по дереву, но ТОЛЬКО когда фокус в дереве.
        QShortcut(
            QKeySequence("Ctrl+F"),
            self.file_tree,
            context=Qt.WidgetWithChildrenShortcut,
            activated=self._toggle_search,
        )

        # Ctrl+Shift+F — поиск по дереву из любого места окна.
        QShortcut(
            QKeySequence("Ctrl+Shift+F"),
            self,
            activated=self._toggle_search,
        )

        # Док диагностики
        self.log_dock = QDockWidget("Диагностика", self)
        self.log_text = DiagnosticsLog()
        self.log_dock.setWidget(self.log_text)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log_dock)
        # Диагностика — компактная полоса внизу, а не половина окна.
        self.resizeDocks([self.log_dock], [90], Qt.Vertical)

    def _init_menu(self):
        menubar = self.menuBar()

        # «Файл» и «Проект» стоят рядом и создаются первыми, чтобы
        # регистрации модулей не меняли их порядок.
        file_menu = menubar.addMenu("Файл")
        project_menu = menubar.addMenu("Проект")
        self._static_menus = {
            "Файл": file_menu,
            "Проект": project_menu,
        }
        self._module_menus = {}
        self._module_menu_actions = []

        # Якоря, перед которыми вставляются пункты модулей. Так порядок
        # статических пунктов не зависит от момента регистрации модуля.
        self._file_menu_anchor = file_menu.addSeparator()

        exit_action = QAction("Выход", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        self.project_create_action = QAction("Создать проект...", self)
        self.project_create_action.triggered.connect(
            self.api.create_project
        )
        project_menu.addAction(self.project_create_action)

        self.project_load_action = QAction("Загрузить проект...", self)
        self.project_load_action.triggered.connect(self.api.load_project)
        project_menu.addAction(self.project_load_action)

        project_menu.addSeparator()

        self.project_settings_action = QAction("Настройки проекта...", self)
        self.project_settings_action.triggered.connect(
            self.api.project_settings
        )
        self.project_settings_action.setEnabled(False)
        self.project_settings_action.setVisible(False)
        project_menu.addAction(self.project_settings_action)

        project_menu.addSeparator()

        self.project_patch_action = QAction("Сформировать патч...", self)
        self.project_patch_action.triggered.connect(
            self.api.build_patch_preview
        )
        self.project_patch_action.setEnabled(False)
        project_menu.addAction(self.project_patch_action)

        project_menu.aboutToShow.connect(self._update_project_menu)

        self._rebuild_module_menus()

        add_listener = getattr(self.api, "add_registration_listener", None)
        if callable(add_listener):
            self._menu_rebuild_sub = add_listener(
                self._rebuild_module_menus
            )

    def _rebuild_module_menus(self):
        """Пересобрать пункты меню, принадлежащие модулям.

        Реестр регистраций меняется при выгрузке/перезагрузке модуля;
        статические пункты платформы при этом не трогаются.
        """
        for menu, action in self._module_menu_actions:
            try:
                menu.removeAction(action)
                action.deleteLater()
            except RuntimeError:
                pass
        self._module_menu_actions = []

        for title, menu in list(self._module_menus.items()):
            if not menu.isEmpty():
                continue
            try:
                self.menuBar().removeAction(menu.menuAction())
                menu.deleteLater()
            except RuntimeError:
                pass
            del self._module_menus[title]

        menus = dict(self._static_menus)
        menus.update(self._module_menus)

        for title, label, callback, shortcut, action in self.api.menu_registrations:
            menu = menus.get(title)
            if menu is None:
                menu = self.menuBar().addMenu(title)
                menus[title] = menu
                self._module_menus[title] = menu

            item = QAction(label, self)
            if shortcut:
                item.setShortcut(shortcut)
            item.triggered.connect(
                lambda _=False, cb=callback, act=action: self.api.trigger_action(
                    act,
                    cb,
                )
            )

            anchor = self._menu_anchor(title)
            if anchor is not None and anchor in menu.actions():
                menu.insertAction(anchor, item)
            else:
                menu.addAction(item)
            self._module_menu_actions.append((menu, item))

    def _menu_anchor(self, title):
        if title == "Файл":
            return self._file_menu_anchor
        if title == "Проект":
            return self.project_create_action
        return None

    def _init_statusbar(self):
        self.statusBar().showMessage("Готов")

        self.project_label = QLabel("Проект: не создан")
        self.statusBar().addPermanentWidget(self.project_label)

        self.prebuild_panel = PrebuildPanel(self)
        self.statusBar().addPermanentWidget(self.prebuild_panel)

        self.patch_write_label = QLabel()
        if PATCH_WRITE_EXPERIMENTAL:
            self.patch_write_label.setText("Запись патчей: экспериментальная")
            self.patch_write_label.setToolTip(
                "Запись включена в экспериментальном режиме."
            )
        else:
            self.patch_write_label.setText("Запись патчей: выключена")
            self.patch_write_label.setToolTip(
                "До Этапа 5 доступен только предпросмотр патча."
            )
        self.statusBar().addPermanentWidget(self.patch_write_label)

        self._prebuild_timer = QTimer(self)
        self._prebuild_timer.setInterval(250)
        self._prebuild_timer.timeout.connect(self._update_prebuild)
        self._prebuild_timer.start()

        # Автосохранение черновиков проекта раз в минуту.
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(60_000)
        self._autosave_timer.timeout.connect(self.api.autosave_project)
        self._autosave_timer.start()

    def set_project_status(self, text):
        self.project_label.setText(str(text))

    def set_patch_rebuild_label(self, rebuild):
        """Переключить пункт меню между «Сформировать» и «Пересобрать»."""
        self.project_patch_action.setText(
            "Пересобрать патч..." if rebuild else "Сформировать патч..."
        )

    def _update_project_menu(self):
        active = bool(self.api.is_project_active())
        self.project_settings_action.setVisible(active)
        self.project_settings_action.setEnabled(active)
        self.project_patch_action.setEnabled(
            active and self.api.project_has_changes()
        )

    # Публичное имя для PlatformApi: окно не обязано вызывать private.
    def refresh_project_actions(self):
        self._update_project_menu()

    def _update_prebuild(self):
        services = getattr(self.api, "services", None)
        status_fn = getattr(services, "prebuild_status", None)
        if not callable(status_fn):
            self.prebuild_panel.setVisible(False)
            return

        try:
            status = status_fn()
        except Exception:
            return

        self.prebuild_panel.set_status(
            status.get("state", "idle"),
            status.get("done", 0),
            status.get("total", 0),
            status.get("error"),
        )

    # -------------------------------------------------- поиск
    def _toggle_search(self):
        if self.search_bar.isVisible():
            self.search_bar.entry.setFocus()
            self.search_bar.entry.selectAll()
        else:
            self.search_bar.show_and_focus()

    def _search_by_name(self, query):
        forest = self.file_tree.forest
        if forest is None:
            return []
        return self.api.services.tree_query.search_nodes(forest, query)

    # -------------------------------------------------- события
    def _on_tree_ready(self, _payload=None):
        forest = self.api.workspace.forest
        if forest is None:
            return
        self.file_tree.load_forest(forest)
        self.search_bar.refresh()

    # -------------------------------------------------- контекстное меню дерева
    def _on_tree_context(self, pos):
        path = self.file_tree.path_at(pos)
        if path is None:
            return

        actions = self.api.context_actions
        if not actions:
            return

        menu = QMenu(self)
        for entry in actions:
            label, handler = entry[0], entry[1]
            predicate = entry[2] if len(entry) > 2 else None
            if predicate is not None and not predicate(path):
                continue

            a = QAction(label, self)
            a.triggered.connect(lambda _=False, h=handler, p=path: h(p))
            menu.addAction(a)

        if not menu.isEmpty():
            menu.exec_(self.file_tree.viewport().mapToGlobal(pos))

    # -------------------------------------------------- drag-and-drop
    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(ITEM_SECTION_MIME):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(ITEM_SECTION_MIME):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasFormat(ITEM_SECTION_MIME):
            # Перенос принят на уровне главного окна, но без побочных
            # действий: обработкой занимаются модули-получатели.
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    # -------------------------------------------------- журнал
    def log(self, msg):
        self.log_text.append_message(str(msg))

    def log_status(self, key, msg):
        """Обновить одну и ту же строку прогресса по ключу."""
        self.log_text.update_message(str(key), msg)

    # -------------------------------------------------- завершение
    def closeEvent(self, event):
        # Сначала спрашиваем владельца состояния: если идёт запись
        # патч-мода, окно не разбирается и закрытие откладывается.
        closers = getattr(self.api, "on_app_close", None)
        if callable(closers):
            try:
                if closers() is False:
                    event.ignore()
                    return
            except Exception:
                pass

        # Окна модулей теперь самостоятельные top-level окна, поэтому
        # приложение не завершится, пока хотя бы одно из них открыто.
        close_windows = getattr(self.api, "close_module_windows", None)
        if callable(close_windows):
            try:
                close_windows()
            except Exception:
                pass

        self._tree_ready_sub.unsubscribe()
        menu_sub = getattr(self, "_menu_rebuild_sub", None)
        if menu_sub is not None:
            menu_sub.unsubscribe()
        if hasattr(self, "_prebuild_timer"):
            self._prebuild_timer.stop()
        if hasattr(self, "_autosave_timer"):
            self._autosave_timer.stop()

        services = getattr(self.api, "services", None)
        stop_fn = getattr(services, "stop_prebuild", None)
        if callable(stop_fn):
            try:
                stop_fn()
            except Exception:
                pass
        super().closeEvent(event)
