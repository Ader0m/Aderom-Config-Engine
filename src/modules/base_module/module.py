# -*- coding: utf-8 -*-
"""
Базовый модуль.

Сценарии:
- «Чистый запуск» (построение дерева + проход Resolver4);
- автоопределение каталога конфигов игровой VFS (./, ../Anomaly 1.5.3 up, ...);
- открытие .ltx через Resolver4 (финальное состояние как в игре);
- контекстные действия дерева;
- граф связей секций.
"""

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QHBoxLayout,
)

from sdk import FileOpenHandler, ModuleBase, ModuleManifest
from sdk.events import Events

DEFAULT_CONFIG_DIR = r"H:\GRA2.5\Anomaly 1.5.3 up\gamedata\configs"


class ResolvedLtxHandler(FileOpenHandler):
    """
    Перехватывает открытие .ltx и показывает финальное состояние файла,
    разрешённое через Resolver4 (так, как его видит игра).
    """

    priority = 200

    def __init__(self, module):
        self.module = module

    def can_open(self, path):
        return Path(path).suffix.lower() == ".ltx"

    def open(self, path, api):
        return self.module.open_resolved(path)


class BaseModule(ModuleBase):
    manifest = ModuleManifest(
        name="base",
        version="0.2.0",
        sdk_min="0.1.0",
        description="Базовый сценарий: чистый запуск, VFS-вид, резолв-открытие.",
    )

    # -------------------------------------------------- жизненный цикл
    def init(self, ctx):
        super().init(ctx)

        self.forest = None
        self._running = False
        self._graph_dialog = None

        self.config_dir = self._detect_config_dir(ctx)
        if self.config_dir:
            ctx.log("Каталог конфигов (VFS игры): %s" % self.config_dir)
        else:
            ctx.log("Каталог конфигов не найден — будет запрошен при запуске.")

        ctx.events.subscribe(Events.APP_STARTED, lambda payload: self.clean_run())

    def register(self, ctx):
        # Меню
        ctx.ui.register_menu("Файл", "Открыть папку...", self.choose_dir, "Ctrl+O")
        ctx.ui.register_menu("Файл", "Чистый запуск", self.clean_run, "Ctrl+Shift+R")
        ctx.ui.register_menu("Инструменты", "Граф связей секции", self.show_section_graph, "Ctrl+G")  # ← новое

        # Пункт меню для сортировки
        ctx.ui.register_menu("Вид", "Сортировать по убыванию (Файлов в дереве)", self.sort_tree_files_desc)

        # Команды
        ctx.commands.register("base.choose_dir", self.choose_dir)
        ctx.commands.register("base.clean_run", self.clean_run)
        ctx.commands.register("base.open_notepad", self.open_notepad)
        ctx.commands.register("base.assemble_open", self.assemble_open)
        ctx.commands.register("base.copy_rel_path", self.copy_rel_path)
        ctx.commands.register("base.open_raw", self.open_raw)
        ctx.commands.register("base.edit_undo", self.edit_undo)
        ctx.commands.register("base.edit_redo", self.edit_redo)
        ctx.commands.register("base.edit_find", self.edit_find)
        ctx.commands.register("base.section_graph", self.show_section_graph)
        ctx.commands.register("base.sort_tree_files_desc", self.sort_tree_files_desc)

        # Обработчик открытия .ltx: резолв через Resolver4
        ctx.ui.register_open_handler(ResolvedLtxHandler(self))

        # Контекстное меню дерева
        ctx.ui.register_tree_context_action("Открыть исходный файл (без резолва)", self.open_raw)
        ctx.ui.register_tree_context_action("Открыть в Notepad++", self.open_notepad)
        ctx.ui.register_tree_context_action("Собрать и открыть в Notepad++", self.assemble_open)
        ctx.ui.register_tree_context_action("Копировать относительный путь", self.copy_rel_path)
        ctx.ui.register_menu("Правка", "Отменить", self.edit_undo)
        ctx.ui.register_menu("Правка", "Повторить", self.edit_redo)
        ctx.ui.register_menu("Правка", "Найти в файле", self.edit_find)

    def activate(self, ctx):
        ctx.log("Базовый модуль активирован.")

    def deactivate(self, ctx):
        ctx.log("Базовый модуль деактивирован.")

    # -------------------------------------------------- VFS-корень
    def _detect_config_dir(self, ctx):
        candidates = [
            Path.cwd(),                                   # ./
            ctx.paths.app_dir,                            # src/
            ctx.paths.app_dir.parent,                     # ../
            ctx.paths.app_dir.parent.parent / "Anomaly 1.5.3 up",
            Path(DEFAULT_CONFIG_DIR),                     # старый хардкод как fallback
        ]
        return ctx.services.detect_config_dir(candidates)

    # -------------------------------------------------- сценарий запуска
    def choose_dir(self):
        d = self.ctx.ui.ask_directory(str(self.config_dir or ""))
        if d:
            self.config_dir = Path(d)
            self.clean_run()

    def clean_run(self):
        if self._running:
            self.ctx.ui.log("Чистый запуск уже выполняется.")
            return

        if not self.config_dir or not self.config_dir.is_dir():
            d = self.ctx.ui.ask_directory("")
            if not d:
                self.ctx.ui.log("Папка не выбрана — запуск отменён.")
                return
            self.config_dir = Path(d)

        self._running = True
        self.ctx.ui.set_status("Сканирование…")
        self.ctx.ui.log("Чистый запуск: %s" % self.config_dir)
        self.ctx.events.publish(Events.CLEAN_RUN_STARTED, str(self.config_dir))

        self.ctx.tasks.run(
            self._scan,
            on_done=self._on_scan_done,
            on_log=self.ctx.ui.log,
        )

    def _scan(self, log):
        sel = self.config_dir

        system = self.ctx.services.find_system_ltx(sel)
        if system is None:
            raise RuntimeError("system.ltx не найден в %s" % sel)

        scan_dir = sel if (sel / "system.ltx").is_file() else system.parent
        log("Сканирую: %s" % scan_dir)

        forest = self.ctx.services.build_tree(scan_dir, system, log=log, clean=True)

        self.ctx.services.resolver.run_pass(forest, self.config_dir, log=log)

        return forest

    def _on_scan_done(self, forest, err):
        self._running = False

        if err is not None:
            self.ctx.ui.set_status("Ошибка")
            self.ctx.ui.show_error(str(err))
            self.ctx.events.publish(Events.ERROR, str(err))
            return

        self.forest = forest
        self.ctx.events.publish(Events.TREE_READY, forest)
        self.ctx.events.publish(Events.CLEAN_RUN_FINISHED, forest)

        self.ctx.ui.set_status(
            "Корней: %d; файлов: %d; в дереве system: %d; секций: %s"
            % (
                len(forest.roots),
                forest.total_files,
                forest.system_tree_files,
                forest.resolver_sections,
            ),
            7000,
        )
        self.ctx.ui.log("Дерево построено. Корней: %d; файлов: %d"
                        % (len(forest.roots), forest.total_files))

    # -------------------------------------------------- граф связей секций
    def show_section_graph(self):
        """Запросить имя секции и построить граф связей."""
        if self.forest is None:
            self.ctx.ui.show_error("Дерево не построено: сначала «Чистый запуск».")
            return

        section_name = self.ctx.ui.ask_text("Имя секции:", "raid_material_1")
        if not section_name:
            return

        self.ctx.ui.set_status("Построение графа: %s…" % section_name)

        def work(log):
            return self.ctx.services.build_section_graph(self.forest, section_name)

        self.ctx.tasks.run(
            work,
            on_done=lambda graph, err: self._on_graph_done(graph, err, section_name),
            on_log=self.ctx.ui.log,
        )

    def _on_graph_done(self, graph, err, section_name):
        """Показать результаты построения графа (немодально)."""
        if err is not None:
            self.ctx.ui.set_status("Ошибка")
            self.ctx.ui.show_error(f"Не удалось построить граф для '{section_name}':\n{err}")
            return

        self.ctx.ui.set_status(f"Граф построен: {len(graph.children)} узлов", 5000)

        if self._graph_dialog is None:
            self._graph_dialog = SectionGraphDialog(self.ctx.ui, graph)
        else:
            self._graph_dialog.update_graph(graph)

        # Немодальный показ: окно не блокирует приложение
        self._graph_dialog.show()
        self._graph_dialog.raise_()
        self._graph_dialog.activateWindow()

    # -------------------------------------------------- открытие файлов
    def open_resolved(self, path):
        """Открыть .ltx: резолв для базовых, сырой вид для патчей."""
        if self.forest is None:
            self.ctx.ui.show_error("Дерево не построено: сначала «Чистый запуск».")
            return False

        path = Path(path)

        if path.name.lower().startswith("mod_"):
            return self._open_as_patch(path)

        return self._open_as_resolved(path)

    def _open_as_patch(self, path):
        """Открыть DLTX-патч: сырое содержимое + информация о корне."""
        self.ctx.ui.set_status("Патч: %s…" % path.name)

        try:
            raw_content = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            self.ctx.ui.show_error(f"Не удалось прочитать: {e}")
            return False

        # Корень уже посчитан TreeBuilder'ом — просто читаем
        parents = self.forest.include_parents.get(path) or ()
        root = sorted(parents, key=lambda x: str(x).lower())[0] if parents else None
        
        # Формируем заголовок
        header = [
            "; ═══════════════════════════════════════════════════════════",
            ";                    DLTX-ПАТЧ",
            "; ═══════════════════════════════════════════════════════════",
            f"; Файл: {path.name}",
            f"; Тип: Патч (не загружается отдельно движком)",
        ]
        
        if root:
            rel_root = self.ctx.services.rel_path(self.forest, root)
            header += [
                f"; Применяется к: {rel_root}",
                "; ",
                "; Чтобы увидеть итоговое состояние с применённым патчем,",
                f"   откройте корневой файл: {root.name}",
            ]
        else:
            header += [
                "; ",
                "; ⚠️ Патч не подключился ни к одному базовому файлу!",
                "; Проверьте имя (должно быть mod_<имя_базы>_<что-то>.ltx)",
            ]
        
        header += [
            "; ═══════════════════════════════════════════════════════════",
            "",
        ]
        
        full_text = "\n".join(header) + raw_content
        
        rel = self.ctx.services.rel_path(self.forest, path)
        title = "DLTX PATCH: %s" % rel
        if not root:
            title = "⚠️ " + title
        
        self.ctx.ui.show_text(full_text, title, readonly=True)
        self.ctx.ui.set_status("Открыт патч: %s" % rel, 5000)
        
        return True

    def _open_as_resolved(self, path):
        """Открыть базовый .ltx через Resolver4."""
        self.ctx.ui.set_status("Резолв: %s…" % path.name)

        def work(log):
            return self.ctx.services.assemble_text(
                path, self.forest, self.config_dir, log=log
            )

        self.ctx.tasks.run(
            work,
            on_done=lambda text, err: self._on_resolved(path, text, err),
            on_log=self.ctx.ui.log,
        )
        return True

    def _on_resolved(self, path, text, err):
        if err is not None:
            self.ctx.ui.set_status("Ошибка")
            self.ctx.ui.show_error("Не удалось разрешить %s:\n%s\n\nИсправьте файл через ПКМ -> Открыть исходный файл" % (path.name, err))
            return

        rel = self.ctx.services.rel_path(self.forest, path)
        self.ctx.ui.show_text(
            text,
            "RESOLVED (как в игре): %s" % rel,
            readonly=True,
        )
        self.ctx.ui.set_status("Открыт резолв: %s" % rel, 5000)

    def open_raw(self, path):
        self.ctx.commands.execute("ui.open_file_raw", path)

    # -------------------------------------------------- контекстные действия
    def open_notepad(self, path):
        ok, msg = self.ctx.services.open_notepad(path)
        if not ok:
            self.ctx.ui.show_error(msg)

    # В методе assemble_open:
    def assemble_open(self, path):
        if self.forest is None:
            self.ctx.ui.show_error("Нет дерева: сначала выполните «Чистый запуск».")
            return

        self.ctx.ui.set_status("Сборка: %s…" % Path(path).name)

        def work(log):
            out = self.ctx.services.assembly.assemble_to_file(path, self.forest)

            ok, msg = self.ctx.services.open_notepad(out)
            if not ok:
                raise RuntimeError(msg)
            return out

        self.ctx.tasks.run(work, on_done=self._on_assemble_done, on_log=self.ctx.ui.log)

    def _on_assemble_done(self, out, err):
        if err is not None:
            self.ctx.ui.set_status("Ошибка")
            self.ctx.ui.show_error(str(err))
            return
        self.ctx.ui.set_status("Открыт: %s" % Path(out).name, 5000)

    def copy_rel_path(self, path):
        if self.forest is None:
            text = str(path)
        else:
            text = self.ctx.services.rel_path(self.forest, Path(path))
        self.ctx.ui.clipboard_set(text)

    def edit_undo(self):
        self.ctx.commands.execute("ui.editor_undo")

    def edit_redo(self):
        self.ctx.commands.execute("ui.editor_redo")

    def edit_find(self):
        self.ctx.commands.execute("ui.editor_find")

    def sort_tree_files_desc(self, path=None):
        """
        Команда для явной сортировки дерева по убыванию количества файлов.
        Использует Duck Typing, чтобы не нарушать контракт SDK и не импортировать UI-виджеты напрямую.
        """
        if self.ctx.ui.window and hasattr(self.ctx.ui.window, "file_tree"):
            tree = self.ctx.ui.window.file_tree
            if hasattr(tree, "sort_by_files_desc"):
                tree.sort_by_files_desc()
            else:
                # Fallback, если метод еще не реализован в UI
                from PyQt5.QtCore import Qt
                tree.setSortingEnabled(True)
                tree.sortByColumn(1, Qt.DescendingOrder)

class SectionGraphDialog(QDialog):
    """Немодальное окно результатов графа связей секции."""

    def __init__(self, ui, graph, parent=None):
        super().__init__(parent)
        self.ui = ui
        self.graph = None

        self.setWindowTitle("Граф связей")
        self.resize(800, 600)

        layout = QVBoxLayout(self)

        self.info_label = QLabel()
        layout.addWidget(self.info_label)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.list_widget)

        button_layout = QHBoxLayout()

        open_btn = QPushButton("Открыть файл")
        open_btn.clicked.connect(self._on_open_clicked)
        button_layout.addWidget(open_btn)

        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.hide)     # hide, не accept/reject
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

        self.update_graph(graph)

    # -------------------------------------------------- наполнение
    def update_graph(self, graph):
        """Полностью пересобрать содержимое окна под новый граф."""
        self.graph = graph

        self.setWindowTitle(f"Граф связей: {graph.section_name}")
        self.info_label.setText(
            f"Секция: {graph.section_name}\n"
            f"Объявлена в: {graph.root.file_path}\n"
            f"Строка: {graph.root.first_match_line}\n"
            f"Найдено файлов: {len(graph.children)}"
        )

        self.list_widget.clear()

        # Корневой узел (объявление)
        root_item = QListWidgetItem(
            f"[ОБЪЯВЛЕНИЕ] {graph.root.file_path.name} "
            f"(строка {graph.root.first_match_line})"
        )
        root_item.setData(Qt.UserRole, graph.root)
        self.list_widget.addItem(root_item)

        # Дочерние узлы
        for node in graph.children:
            if node.raw_ref is not None:
                # Узел текстуры: в графе виден путь из icons_texture = *
                item_text = f"[TEXTURE] {node.raw_ref}"
                tooltip = (
                    f"icons_texture объявлена в {node.file_path.name}, "
                    f"строка {node.first_match_line}"
                )
            else:
                match_types = sorted({m.edge_type.value for m in node.matches})
                item_text = (
                    f"{node.display_name}  "
                    f"[совпадений: {len(node.matches)}, "
                    f"типы: {', '.join(match_types)}, "
                    f"первая строка: {node.first_match_line}]"
                )
                tooltip = str(node.file_path)

            item = QListWidgetItem(item_text)
            item.setData(Qt.UserRole, node)
            item.setToolTip(tooltip)
            self.list_widget.addItem(item)

    # -------------------------------------------------- действия
    def _node_at(self, item):
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def _on_item_double_clicked(self, item):
        """Двойной клик — открыть файл на первой строке совпадения."""
        node = self._node_at(item)
        if node is not None and node.file_path is not None:
            self.ui.commands.execute(
                "ui.open_file_at_line",
                str(node.file_path),
                node.first_match_line,
            )

    def _on_open_clicked(self):
        self._on_item_double_clicked(self.list_widget.currentItem())

MODULE = BaseModule()