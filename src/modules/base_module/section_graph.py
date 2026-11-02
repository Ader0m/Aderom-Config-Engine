# -*- coding: utf-8 -*-
"""Граф связей секции: построение через ядро и собственное окно модуля."""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)


class SectionGraphFeature:
    """Запрашивает секцию, строит граф и показывает его в окне модуля."""

    WINDOW_KEY = "base.section_graph"

    def __init__(self, ctx, session):
        self.ctx = ctx
        self.session = session
        self._window = None

    def show(self):
        if not self.session.has_tree:
            self.ctx.ui.show_error(
                "Дерево не построено: сначала «Чистый запуск»."
            )
            return

        section_name = self.ctx.ui.ask_text(
            "Имя секции:",
            "raid_material_1",
        )
        if not section_name:
            return

        self.ctx.ui.set_status("Построение графа: %s…" % section_name)

        forest = self.session.forest

        def work(log):
            return self.ctx.services.build_section_graph(forest, section_name)

        self.ctx.tasks.run(
            work,
            on_done=lambda graph, err: self._on_done(
                graph,
                err,
                section_name,
            ),
            on_log=self.ctx.ui.log,
        )

    def close(self):
        if self._window is not None:
            self.ctx.ui.close_module_window(self.WINDOW_KEY)
            self._window = None

    def _on_done(self, graph, err, section_name):
        if err is not None:
            self.ctx.ui.set_status("Ошибка")
            self.ctx.ui.show_error(
                "Не удалось построить граф для '%s':\n%s"
                % (section_name, err)
            )
            return

        self.ctx.ui.set_status(
            "Граф построен: %d узлов" % len(graph.children),
            5000,
        )

        if self._window is None:
            self._window = SectionGraphWindow(self.ctx.ui.open_at_line)

        self._window.update_graph(graph)
        self.ctx.ui.show_module_window(self.WINDOW_KEY, self._window)


class SectionGraphWindow(QDialog):
    """Немодальное окно графа. Создаётся и наполняется модулем."""

    def __init__(self, open_at_line, parent=None):
        super().__init__(parent)
        self._open_at_line = open_at_line
        self._graph = None

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
        close_btn.clicked.connect(self.hide)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

    # -------------------------------------------------- наполнение
    def update_graph(self, graph):
        """Полностью пересобрать содержимое окна под новый граф."""
        self._graph = graph

        self.setWindowTitle("Граф связей: %s" % graph.section_name)
        self.info_label.setText(
            "Секция: %s\n"
            "Объявлена в: %s\n"
            "Строка: %s\n"
            "Найдено файлов: %d"
            % (
                graph.section_name,
                _path_text(graph.root.file_path),
                graph.root.first_match_line,
                len(graph.children),
            )
        )

        self.list_widget.clear()

        root_item = QListWidgetItem(
            "[ОБЪЯВЛЕНИЕ] %s (строка %s)"
            % (_path_name(graph.root.file_path), graph.root.first_match_line)
        )
        root_item.setData(Qt.UserRole, graph.root)
        root_item.setToolTip(_path_text(graph.root.file_path))
        self.list_widget.addItem(root_item)

        for node in graph.children:
            if node.is_texture:
                item_text = "[TEXTURE] %s" % node.raw_ref
                tooltip = (
                    "icons_texture объявлена в %s, строка %s"
                    % (_path_name(node.file_path), node.first_match_line)
                )
            else:
                item_text = (
                    "%s  [совпадений: %d, типы: %s, первая строка: %s]"
                    % (
                        node.display_name,
                        node.matches_count,
                        ", ".join(node.edge_types),
                        node.first_match_line,
                    )
                )
                tooltip = _path_text(node.file_path)

            item = QListWidgetItem(item_text)
            item.setData(Qt.UserRole, node)
            item.setToolTip(tooltip)
            self.list_widget.addItem(item)

    # -------------------------------------------------- действия
    def _on_item_double_clicked(self, item):
        node = item.data(Qt.UserRole) if item is not None else None
        if node is None or node.file_path is None:
            return

        self._open_at_line(node.file_path, node.first_match_line)

    def _on_open_clicked(self):
        self._on_item_double_clicked(self.list_widget.currentItem())


def _path_text(path) -> str:
    return str(path) if path is not None else "—"


def _path_name(path) -> str:
    return path.name if path is not None else "—"
