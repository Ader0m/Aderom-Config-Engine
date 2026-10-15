# -*- coding: utf-8 -*-
"""
Главное окно — оболочка рабочей среды (платформа).

Здесь НЕТ бизнес-сценариев:
- нет «Чистого запуска»;
- нет config_dir;
- нет кэша;
- нет импорта core.

Меню и контекстные действия собираются из регистраций модулей.
Дерево обновляется по событию TREE_READY.
Открытие файла идёт через команду ui.open_file и маршрутизатор.
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAction,
    QDockWidget,
    QMainWindow,
    QMenu,
    QSplitter,
    QTextEdit,
)

from sdk.events import Events

from .editor import CodeEditor
from .file_open_router import TextEditorHandler
from .file_tree import FileTreeWidget


class MainWindow(QMainWindow):
    def __init__(self, api):
        super().__init__()

        self.api = api

        self.setWindowTitle("LTX Explorer")
        self.resize(1400, 900)

        self._init_ui()
        self._init_menu()
        self._init_statusbar()

        # Платформа реагирует на события ядра/модулей.
        self.api.events.subscribe(Events.TREE_READY, self._on_tree_ready)

        self.api.bind_window(self)

    # -------------------------------------------------- UI
    def _init_ui(self):
        self.splitter = QSplitter(Qt.Horizontal)

        self.file_tree = FileTreeWidget(sort_fn=self.api.services.sort_nodes)
        self.file_tree.file_opened.connect(
            lambda p: self.api.commands.execute("ui.open_file", p)
        )
        self.file_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_tree.customContextMenuRequested.connect(self._on_tree_context)

        self.editor = CodeEditor()

        # Обработчик открытия по умолчанию — текстовый редактор.
        self.api.router.set_default(TextEditorHandler(self.editor))

        self.splitter.addWidget(self.file_tree)
        self.splitter.addWidget(self.editor)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 3)

        self.setCentralWidget(self.splitter)

        self.log_dock = QDockWidget("Диагностика", self)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_dock.setWidget(self.log_text)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log_dock)

    def _init_menu(self):
        menubar = self.menuBar()
        menus = {}

        # Меню собираются из регистраций модулей.
        for title, label, callback, shortcut in self.api.menu_registrations:
            m = menus.get(title)
            if m is None:
                m = menubar.addMenu(title)
                menus[title] = m

            a = QAction(label, self)
            if shortcut:
                a.setShortcut(shortcut)
            a.triggered.connect(lambda _=False, cb=callback: cb())
            m.addAction(a)

        file_menu = menus.get("Файл") or menubar.addMenu("Файл")
        file_menu.addSeparator()

        exit_action = QAction("Выход", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

    def _init_statusbar(self):
        self.statusBar().showMessage("Готов")

    # -------------------------------------------------- события
    def _on_tree_ready(self, forest):
        self.file_tree.load_forest(forest)

    # -------------------------------------------------- контекстное меню дерева
    def _on_tree_context(self, pos):
        path = self.file_tree.path_at(pos)
        if path is None:
            return

        actions = self.api.context_actions
        if not actions:
            return

        menu = QMenu(self)
        for label, handler in actions:
            a = QAction(label, self)
            a.triggered.connect(lambda _=False, h=handler, p=path: h(p))
            menu.addAction(a)

        menu.exec_(self.file_tree.viewport().mapToGlobal(pos))

    # -------------------------------------------------- журнал
    def log(self, msg):
        self.log_text.append(str(msg))