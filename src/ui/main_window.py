# -*- coding: utf-8 -*-
"""
Главное окно — оболочка рабочей среды (платформа).

Здесь НЕТ бизнес-сценариев.
Поиск по имени файла — платформенная возможность дерева:
запрос данных идёт через api.services (TreeQueryService.search_nodes).
"""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QAction,
    QDockWidget,
    QMainWindow,
    QMenu,
    QShortcut,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QLineEdit,
    QPushButton,
    QHBoxLayout,
)

from sdk.events import Events

from .editor import CodeEditor
from .file_open_router import TextEditorHandler
from .file_tree import FileTreeWidget
from .search_bar import SearchBar
from .reference_graph_widget import ReferenceGraphWidget

from .tasks import TaskService

class MainWindow(QMainWindow):
    def __init__(self, api):
        super().__init__()

        self.api = api

        self.setWindowTitle("LTX Explorer")
        self.resize(1400, 900)

        self.tasks = TaskService()
        self._graph_task = None

        self._init_ui()
        self._init_menu()
        self._init_statusbar()

        self.api.events.subscribe(Events.TREE_READY, self._on_tree_ready)

        self.api.bind_window(self)

    # -------------------------------------------------- UI
    def _init_ui(self):
        self.splitter = QSplitter(Qt.Horizontal)

        # Дерево + строка поиска в левой панели
        self.file_tree = FileTreeWidget(sort_fn=self.api.services.sort_nodes)
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
        self.editor = CodeEditor()
        self.api.router.set_default(TextEditorHandler(self.editor))

        self.splitter.addWidget(left)
        self.splitter.addWidget(self.editor)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 3)

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
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_dock.setWidget(self.log_text)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log_dock)

        # Док графа связей
        self._init_reference_graph_dock()

    def _init_reference_graph_dock(self):
        """Инициализирует dock widget с графом связей."""
        self.graph_dock = QDockWidget("Граф связей", self)

        # Панель ввода секции
        graph_widget = QWidget()
        graph_layout = QVBoxLayout(graph_widget)

        # Ввод имени секции
        input_layout = QHBoxLayout()
        self.section_input = QLineEdit()
        self.section_input.setPlaceholderText("Введите имя секции...")
        self.section_input.returnPressed.connect(self._build_graph)

        self.build_graph_btn = QPushButton("Построить")
        self.build_graph_btn.clicked.connect(self._build_graph)

        input_layout.addWidget(self.section_input)
        input_layout.addWidget(self.build_graph_btn)
        graph_layout.addLayout(input_layout)

        # Виджет графа
        self.reference_graph = ReferenceGraphWidget()
        self.reference_graph.file_open_requested.connect(self._on_graph_file_open)
        self.reference_graph.section_selected.connect(self._on_graph_section_selected)
        graph_layout.addWidget(self.reference_graph, 1)

        self.graph_dock.setWidget(graph_widget)
        self.addDockWidget(Qt.RightDockWidgetArea, self.graph_dock)

    def _init_menu(self):
        menubar = self.menuBar()
        menus = {}

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
        return self.api.services.search_nodes(forest, query)

    # -------------------------------------------------- граф связей
    def _build_graph(self):
        """Строит граф в фоновом потоке, не блокируя интерфейс."""
        section_name = self.section_input.text().strip()
        if not section_name:
            return

        forest = self.file_tree.forest
        if forest is None:
            self.log("Ошибка: дерево не загружено")
            return

        self.build_graph_btn.setEnabled(False)
        self.log(f"Построение графа для секции [{section_name}]...")

        def work(log):
            return self.api.services.build_reference_graph(forest, section_name)

        def done(result, error):
            self.build_graph_btn.setEnabled(True)

            if error is not None:
                self.log(f"Ошибка построения графа: {error}")
                return

            self.reference_graph.display_graph(section_name, result)

            if hasattr(result, "node_count"):
                self.log(
                    f"Граф построен: {result.node_count} узлов, "
                    f"{result.edge_count} рёбер"
                )
            else:
                self.log(f"Граф не построен: {result.message}")

        self._graph_task = self.tasks.run(work, on_done=done, on_log=self.log)
        
    def _on_graph_file_open(self, file_path):
        """Открытие файла из графа связей."""
        self.api.commands.execute("ui.open_file", file_path)

    def _on_graph_section_selected(self, section_name):
        """Навигация к секции из графа связей."""
        self.section_input.setText(section_name)
        self._build_graph()

    # -------------------------------------------------- события
    def _on_tree_ready(self, forest):
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
        for label, handler in actions:
            a = QAction(label, self)
            a.triggered.connect(lambda _=False, h=handler, p=path: h(p))
            menu.addAction(a)

        menu.exec_(self.file_tree.viewport().mapToGlobal(pos))

    # -------------------------------------------------- журнал
    def log(self, msg):
        self.log_text.append(str(msg))