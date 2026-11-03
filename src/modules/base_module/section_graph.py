# -*- coding: utf-8 -*-
"""Граф связей секции: построение через ядро и собственное окно модуля."""

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)


class SectionGraphFeature:
    """Запрашивает секцию, строит граф и показывает его в окне модуля."""

    WINDOW_KEY = "base.section_graph"

    def __init__(self, ctx, session):
        self.ctx = ctx
        self.session = session
        self._window = None
        self._current_section = None
        self._request_seq = 0

    def show(self):
        if not self.session.has_tree:
            self.ctx.ui.show_error(
                "Дерево не построено: сначала «Чистый запуск»."
            )
            return

        section_name = self.ctx.ui.ask_text(
            "Имя секции:",
            self._current_section or "raid_material_1",
        )
        if not section_name:
            return

        self._current_section = section_name
        vfs_mode = (
            self._window.vfs_enabled()
            if self._window is not None
            else False
        )
        self._request_graph(section_name, vfs_mode)

    def close(self):
        if self._window is not None:
            self.ctx.ui.close_module_window(self.WINDOW_KEY)
            self._window = None

    # -------------------------------------------------- запросы
    def _request_graph(self, section_name, vfs_mode):
        self._request_seq += 1
        token = self._request_seq
        progress_key = "base.section_graph.progress.%d" % token
        forest = self.session.forest

        suffix = " (VFS)" if vfs_mode else ""
        self.ctx.ui.set_status(
            "Построение графа%s: %s…" % (suffix, section_name)
        )
        self.ctx.ui.log(
            "Граф связей%s: поиск '%s'…" % (suffix, section_name)
        )

        def work(log):
            # Граф не должен конкурировать с фоновой сборкой и не
            # перезапускать её: ждём завершения, затем идём по кэшу.
            self.ctx.services.wait_prebuild(log)
            return self.ctx.services.build_section_graph(
                forest,
                section_name,
                vfs_mode=vfs_mode,
                log=log,
            )

        def on_log(message, key=progress_key):
            self._on_graph_log(message, key)

        self.ctx.tasks.run(
            work,
            on_done=lambda graph, err: self._on_done(
                graph,
                err,
                section_name,
                vfs_mode,
                token,
            ),
            on_log=on_log,
        )

    def _on_graph_log(self, message, progress_key):
        """Прогресс/кэш графа — одной обновляемой строкой, не потоком строк."""
        message = str(message)
        if (
            message.startswith("resolved graph")
            or message.startswith("resolve cache:")
            or message.startswith("Ожидание фоновой сборки")
            or message.startswith("Фоновая сборка завершена")
        ):
            self.ctx.ui.log_status(
                progress_key,
                message,
            )
        else:
            self.ctx.ui.log(message)

    def _on_vfs_toggled(self, checked):
        """Переключение режима: повторяем запрос для текущей секции."""
        if not self._current_section:
            return
        self._request_graph(self._current_section, bool(checked))

    def _on_done(self, graph, err, section_name, vfs_mode, token):
        if token != self._request_seq:
            return

        if err is not None:
            self.ctx.ui.set_status("Ошибка")
            self.ctx.ui.log(
                "Ошибка графа связей '%s': %s"
                % (section_name, err)
            )
            self.ctx.ui.show_error(
                "Не удалось построить граф для '%s':\n%s"
                % (section_name, err)
            )
            return

        main, lost = _split_children(graph)
        status = "Граф построен: %d узлов" % len(main)
        if vfs_mode:
            status += "; потерянных: %d" % len(lost)
        self.ctx.ui.set_status(status, 5000)
        self.ctx.ui.log(
            "Граф связей '%s'%s: узлов %d%s"
            % (
                section_name,
                " (VFS)" if vfs_mode else "",
                len(main),
                "; потерянных: %d" % len(lost) if vfs_mode else "",
            )
        )

        if self._window is None:
            self._window = SectionGraphWindow(
                self.ctx.ui.open_at_line,
                open_raw_at_line=self.ctx.ui.open_raw_at_line,
                open_resolved_at_line=self._open_resolved_at_line,
                on_vfs_toggled=self._on_vfs_toggled,
            )

        self._window.update_graph(graph, vfs_mode=vfs_mode)
        self.ctx.ui.show_module_window(self.WINDOW_KEY, self._window)

    def _open_resolved_at_line(self, path, line_number):
        """Открыть итоговый файл резолвера на строке совпадения."""
        self.ctx.commands.execute(
            "base.open_ltx_resolved_at_line",
            path,
            line_number,
        )


class SectionGraphWindow(QDialog):
    """Немодальное окно графа. Создаётся и наполняется модулем."""

    def __init__(self, open_at_line, open_raw_at_line=None,
                 open_resolved_at_line=None, on_vfs_toggled=None,
                 parent=None):
        super().__init__(parent)
        self._open_at_line = open_at_line
        self._open_raw_at_line = open_raw_at_line
        self._open_resolved_at_line = open_resolved_at_line
        self._on_vfs_toggled = on_vfs_toggled
        self._graph = None
        self._vfs_mode = False
        self._lost_panel_expanded = False

        self.setWindowTitle("Граф связей")
        self.resize(800, 600)

        layout = QVBoxLayout(self)

        # Информационный блок слева, чекбокс режима — справа от него,
        # прижат к правому краю и отцентрован по вертикали.
        self.info_label = QLabel()

        self.vfs_check_box = QCheckBox("Поиск перезаписанных связей")
        self.vfs_check_box.toggled.connect(self._on_vfs_check_box_toggled)

        info_row = QHBoxLayout()
        info_row.addWidget(self.info_label, 1)
        info_row.addWidget(
            self.vfs_check_box, 0, Qt.AlignRight | Qt.AlignVCenter
        )
        layout.addLayout(info_row)

        self.splitter = QSplitter(Qt.Horizontal)

        main_panel = QWidget()
        main_layout = QVBoxLayout(main_panel)
        main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_title = QLabel("Итоговое дерево")
        main_layout.addWidget(self.main_title)
        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.list_widget.itemClicked.connect(
            lambda _item: self._set_active_list(self.list_widget)
        )
        main_layout.addWidget(self.list_widget)
        self.splitter.addWidget(main_panel)

        self.lost_panel = QWidget()
        lost_layout = QVBoxLayout(self.lost_panel)
        lost_layout.setContentsMargins(0, 0, 0, 0)
        lost_layout.addWidget(QLabel("Потерянные связи (VFS)"))
        self.lost_list_widget = QListWidget()
        self.lost_list_widget.itemDoubleClicked.connect(
            self._on_item_double_clicked
        )
        self.lost_list_widget.itemClicked.connect(
            lambda _item: self._set_active_list(self.lost_list_widget)
        )
        lost_layout.addWidget(self.lost_list_widget)
        self.splitter.addWidget(self.lost_panel)
        self.lost_panel.setVisible(False)

        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        layout.addWidget(self.splitter, 1)

        self._active_list = self.list_widget

        button_layout = QHBoxLayout()

        open_btn = QPushButton("Открыть совпадение")
        open_btn.setToolTip(
            "Обычный режим: итоговый файл резолвера; VFS: исходный файл"
        )
        open_btn.clicked.connect(self._on_open_clicked)
        button_layout.addWidget(open_btn)

        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.hide)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

    # -------------------------------------------------- режим
    def vfs_enabled(self):
        return self.vfs_check_box.isChecked()

    def _on_vfs_check_box_toggled(self, checked):
        if not checked:
            self.lost_panel.setVisible(False)
        if self._on_vfs_toggled is not None:
            self._on_vfs_toggled(bool(checked))

    # -------------------------------------------------- наполнение
    def update_graph(self, graph, vfs_mode=False):
        """Полностью пересобрать содержимое окна под новый граф."""
        self._graph = graph
        self._vfs_mode = bool(vfs_mode)

        title = "Граф связей"
        if self._vfs_mode:
            title += " (VFS)"
        self.setWindowTitle("%s: %s" % (title, graph.section_name))

        # Найденные и потерянные связи не пересекаются: потерянные
        # показываются только в правой панели, в левый список не попадают.
        main, lost = _split_children(graph)

        info = (
            "Секция: %s\n"
            "Объявлена в: %s\n"
            "Строка: %s\n"
            "Найдено файлов: %d"
            % (
                graph.section_name,
                _path_text(graph.root.file_path),
                graph.root.first_match_line,
                len(main),
            )
        )
        if self._vfs_mode:
            info += (
                "\nРежим: вся VFS без фильтра LtxForest"
                "\nПотерянных связей: %d" % len(lost)
            )
        else:
            info += "\nРежим: итоговое дерево"
        self.info_label.setText(info)

        self._populate_list(self.list_widget, main, include_root=True)
        self.lost_list_widget.clear()

        if self._vfs_mode:
            self._populate_list(
                self.lost_list_widget,
                lost,
                include_root=False,
            )
            if not self.lost_panel.isVisible():
                self.lost_panel.setVisible(True)
                if not self._lost_panel_expanded:
                    self.resize(self.width() + 420, self.height())
                    self._lost_panel_expanded = True
        else:
            self.lost_panel.setVisible(False)

        self._set_active_list(self.list_widget)

    def _populate_list(self, list_widget, nodes, include_root):
        list_widget.clear()

        if include_root:
            list_widget.addItem(self._make_root_item(self._graph.root))

        for node in nodes:
            list_widget.addItem(self._make_item(node))

    def _make_root_item(self, node):
        line_label = (
            "строка резолва"
            if getattr(node, "resolved", False)
            else "исходная строка"
        )
        item = QListWidgetItem(
            "[ОБЪЯВЛЕНИЕ] %s (%s %s)"
            % (_path_name(node.file_path), line_label, node.first_match_line)
        )
        item.setData(Qt.UserRole, node)
        item.setToolTip(_path_text(node.file_path))
        return item

    def _make_item(self, node):
        if node.is_texture:
            item_text = "[TEXTURE] %s" % node.raw_ref
            tooltip = (
                "icons_texture объявлена в %s, строка %s"
                % (_path_name(node.file_path), node.first_match_line)
            )
        else:
            line_label = (
                "строка резолва"
                if getattr(node, "resolved", False)
                else "исходная строка"
            )
            item_text = (
                "%s  [совпадений: %d, типы: %s, %s: %s]"
                % (
                    node.display_name,
                    node.matches_count,
                    ", ".join(node.edge_types),
                    line_label,
                    node.first_match_line,
                )
            )
            tooltip = _path_text(node.file_path)

        item = QListWidgetItem(item_text)
        item.setData(Qt.UserRole, node)
        item.setToolTip(tooltip)
        return item

    # -------------------------------------------------- действия
    def _set_active_list(self, list_widget):
        self._active_list = list_widget

    def _on_item_double_clicked(self, item):
        node = item.data(Qt.UserRole) if item is not None else None
        if node is None or node.file_path is None:
            return

        resolved = bool(getattr(node, "resolved", False))

        if resolved and self._open_resolved_at_line is not None:
            # Строка относится к итоговому файлу после резолва.
            self._open_resolved_at_line(node.file_path, node.first_match_line)
        elif self._open_raw_at_line is not None:
            # VFS/скрипты: строка относится к исходному файлу.
            self._open_raw_at_line(node.file_path, node.first_match_line)
        else:
            self._open_at_line(node.file_path, node.first_match_line)

    def _on_open_clicked(self):
        self._on_item_double_clicked(self._active_list.currentItem())


def _node_key(node):
    """Ключ узла: файл + строка + ссылка, для сравнения узлов между собой."""
    return (
        str(node.file_path) if node.file_path is not None else None,
        getattr(node, "first_match_line", None),
        getattr(node, "raw_ref", None),
        getattr(node, "display_name", None),
    )


def _split_children(graph):
    """Разделить детей графа на (найденные, потерянные) без пересечений.

    Потерянные (перезаписанные) связи исключаются из общего списка,
    чтобы не дублироваться в левой колонке окна.
    """
    lost = list(getattr(graph, "lost_children", ()) or ())
    lost_keys = {_node_key(node) for node in lost}
    main = [
        node for node in graph.children
        if _node_key(node) not in lost_keys
    ]
    return main, lost


def _path_text(path) -> str:
    return str(path) if path is not None else "—"


def _path_name(path) -> str:
    return Path(path).name if path is not None else "—"