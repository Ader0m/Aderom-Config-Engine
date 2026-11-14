# -*- coding: utf-8 -*-
"""Граф связей секции: построение через ядро и собственное окно модуля."""

import tempfile
from dataclasses import replace
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sdk import SectionGraphRequest, path_key as _path_key

# Маркеры и цвета типов связей. Цвета запрашиваются через ctx.ui,
# чтобы модуль не импортировал внутренности UI.
EDGE_STYLES = {
    "declaration": ("Объявление", "graph_declaration"),
    "inheritance": ("Наследование", "graph_inheritance"),
    "ltx_reference": ("LTX", "graph_ltx"),
    "script_reference": ("Скрипт", "graph_script"),
    "xml_reference": ("XML", "graph_xml"),
    "texture_reference": ("Текстура", "graph_texture"),
    "visual_reference": ("Модель", "graph_visual"),
}


def _edge_style(edge_type, color_fn=None, styles=None):
    """(маркер, QColor|None) для типа связи."""
    entry = (styles or EDGE_STYLES).get(str(edge_type or ""))
    if entry is None:
        label, attr = str(edge_type or "Связь"), None
    elif hasattr(entry, "label"):
        label, attr = entry.label, entry.color_key
    else:
        label, attr = entry
    color = color_fn(attr) if (color_fn and attr) else None
    return label, QColor(color) if color else None


class GraphTree(QTreeWidget):
    """QTreeWidget с алиасами count/item для совместимости с QListWidget."""

    def count(self):
        return self.topLevelItemCount()

    def item(self, index):
        return self.topLevelItem(index)


def _node_file_name(node):
    """Только имя файла: полный путь остаётся в tooltip и в node."""
    if getattr(node, "primary_edge_type", "") == "inheritance":
        related = getattr(node, "related_name", None)
        if related:
            return related
    target = getattr(node, "target_path", None)
    if target is not None:
        return Path(target).name
    if getattr(node, "file_path", None) is not None:
        return Path(node.file_path).name
    return getattr(node, "display_name", None) or "—"


def _is_materialized(path) -> bool:
    """Путь лежит во временном каталоге копий VFS-ассетов."""
    try:
        root = Path(tempfile.gettempdir()) / "ace_assets"
        return str(path).lower().startswith(str(root).lower())
    except Exception:
        return False


class SectionGraphFeature:
    """Запрашивает секцию, строит граф и показывает его в окне модуля."""

    WINDOW_KEY = "base.section_graph"

    def __init__(self, ctx, session, open_raw=None):
        self.ctx = ctx
        self.session = session
        self._open_raw = open_raw
        self._window = None
        self._current_section = None
        self._request_seq = 0
        self._live_graph = None
        self._live_section = None
        self._live_match_keys = set()
        self._live_xml_files = set()
        self._live_node_keys = set()

    def show(self):
        if not self.session.has_tree:
            self.ctx.ui.show_error(
                "Дерево не построено: сначала «Пересобрать VFS»."
            )
            return

        section_name = self.ctx.ui.ask_text(
            "Имя секции:",
            self._current_section or "raid_material_1",
        )
        if not section_name:
            return

        self._current_section = section_name
        if self._window is not None and self._window.vfs_enabled():
            self._window.vfs_check_box.setChecked(False)
        self._request_graph(section_name, False)

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
        overlay_fn = getattr(
            self.ctx.ui,
            "project_overlay",
            None,
        )
        overlay = overlay_fn() if callable(overlay_fn) else None

        suffix = " (VFS)" if vfs_mode else ""
        self.ctx.ui.set_status(
            "Построение связей%s: %s…" % (suffix, section_name)
        )
        self.ctx.ui.log(
            "Связи секции%s: поиск '%s'…" % (suffix, section_name)
        )

        def work(log):
            # Обычный граф идёт по кэшу резолвера, поэтому сначала
            # гарантируем актуальную предсборку. VFS-режим читает сырые
            # файлы и в резолве не нуждается.
            if not vfs_mode:
                ensure = getattr(
                    self.ctx.services,
                    "ensure_prebuild",
                    None,
                )
                if callable(ensure):
                    ensure(
                        forest,
                        self.session.config_dir,
                        overlay=overlay,
                    )
                self.ctx.services.wait_prebuild(
                    log,
                    timeout=120,
                )
            graph = self.ctx.services.build_section_graph(
                forest,
                section_name,
                vfs_mode=vfs_mode,
                log=log,
                overlay=overlay,
            )

            # Дополнительные связи от модулей (например, .ogg из snd_*).
            contribution = None
            collect = getattr(
                self.ctx.services,
                "collect_section_graph_contribution",
                None,
            )
            if callable(collect):
                try:
                    contribution = collect(
                        SectionGraphRequest(
                            forest=forest,
                            section_name=section_name,
                            graph=graph,
                            vfs_mode=vfs_mode,
                            overlay=overlay,
                        ),
                        log=log,
                    )
                except Exception as exc:
                    log("связи секции: дополнительный поставщик: %s" % exc)
                    contribution = None
            return graph, contribution

        def on_log(message, key=progress_key):
            self._on_graph_log(message, key)

        self.ctx.tasks.run(
            work,
            on_done=lambda result, err: self._on_done(
                result,
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
            or message.startswith("resolved xml")
            or message.startswith("resolve cache:")
            or message.startswith("Ожидание фоновой сборки")
            or message.startswith("Фоновая сборка завершена")
        ):
            self.ctx.ui.log_status(progress_key, message)
        else:
            self.ctx.ui.log(message)

    def _on_vfs_toggled(self, checked):
        """Включить/выключить поиск потерянных, не трогая левый граф."""
        if not checked:
            self._request_seq += 1
            if self._window is not None:
                self._window.clear_lost_nodes()
            return
        if not self._current_section:
            return
        self._request_graph(self._current_section, True)

    def _on_window_message(self, message):
        """Сообщение окна графа: в журнал, статус и диалог при ошибке."""
        message = str(message)
        self.ctx.ui.log(message)
        self.ctx.ui.set_status(message.splitlines()[0], 5000)
        low = message.lower()
        if "не найден" in low or "не удалось" in low:
            self.ctx.ui.show_error(message)

    def _on_done(self, result, err, section_name, vfs_mode, token):
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

        graph, contribution = result
        edge_styles = dict(
            getattr(contribution, "edge_styles", None) or {}
        )
        openers = dict(
            getattr(contribution, "openers", None) or {}
        )

        if self._window is None:
            self._window = SectionGraphWindow(
                self.ctx.ui.open_at_line,
                open_raw=self._open_raw,
                on_vfs_toggled=self._on_vfs_toggled,
                open_with_shell=self.ctx.services.open_with_shell,
                on_message=self._on_window_message,
                theme_color=self.ctx.ui.theme_color,
            )

        if not vfs_mode:
            if contribution is not None and contribution.children:
                graph = replace(
                    graph,
                    children=tuple(graph.children)
                    + tuple(contribution.children),
                )
            self._live_graph = graph
            self._live_section = section_name
            self._live_match_keys, self._live_xml_files = (
                _live_graph_state(graph)
            )
            self._live_node_keys = {
                _node_key(node) for node in graph.children
            }
            self._window.update_graph(
                graph,
                vfs_mode=False,
                edge_styles=edge_styles,
                openers=openers,
            )
            status = "Связи секции: %d узлов" % len(graph.children)
            self.ctx.ui.set_status(status, 5000)
            self.ctx.ui.log(
                "Связи секции '%s': узлов %d"
                % (section_name, len(graph.children))
            )
        else:
            if (
                self._live_graph is None
                or self._live_section != section_name
            ):
                self.ctx.ui.set_status(
                    "Сначала постройте итоговые связи",
                    5000,
                )
                self._window.clear_lost_nodes()
                return

            raw_lost = _lost_from_raw(
                graph,
                self._live_match_keys,
                self._live_xml_files,
            )
            extra = (
                tuple(contribution.lost_children)
                if contribution is not None
                else ()
            )
            lost = _unique_lost(
                raw_lost + list(extra),
                self._live_node_keys,
            )
            self._window.set_lost_nodes(
                lost,
                edge_styles=edge_styles,
                openers=openers,
            )
            self.ctx.ui.set_status(
                "Потерянные связи: %d" % len(lost),
                5000,
            )
            self.ctx.ui.log(
                "Связи секции '%s' (VFS): потерянных %d"
                % (section_name, len(lost))
            )

        self.ctx.ui.show_module_window(self.WINDOW_KEY, self._window)


class SectionGraphWindow(QDialog):
    """Немодальное окно графа. Создаётся и наполняется модулем."""

    def __init__(self, open_at_line, open_raw=None,
                 on_vfs_toggled=None, parent=None,
                 open_with_shell=None, on_message=None,
                 theme_color=None):
        super().__init__(parent)
        self._open_at_line = open_at_line
        self._open_raw = open_raw
        self._on_vfs_toggled = on_vfs_toggled
        self._open_with_shell = open_with_shell
        self._on_message = on_message
        self._theme_color = theme_color
        self._graph = None
        self._vfs_mode = False
        self._lost_panel_expanded = False
        self._edge_styles = dict(EDGE_STYLES)
        self._openers = {}

        self.setWindowTitle("Связи секции")
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
        self.list_widget = self._make_tree()
        main_layout.addWidget(self.list_widget)
        self.splitter.addWidget(main_panel)

        self.lost_panel = QWidget()
        lost_layout = QVBoxLayout(self.lost_panel)
        lost_layout.setContentsMargins(0, 0, 0, 0)
        lost_layout.addWidget(QLabel("Потерянные связи (VFS)"))
        self.lost_list_widget = self._make_tree()
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
            "Обычный режим: итоговый файл резолвера; VFS: исходный файл.\n"
            "Текстуры и модели открываются приложением Windows."
        )
        open_btn.clicked.connect(self._on_open_clicked)
        button_layout.addWidget(open_btn)

        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.hide)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

    def _make_tree(self):
        """Плоский список связей с колонками совпадений и первой строки."""
        tree = GraphTree()
        tree.setColumnCount(3)
        tree.setHeaderLabels(["Файл / связь", "Совпадений", "Строка"])
        tree.setRootIsDecorated(False)
        tree.setAlternatingRowColors(True)
        tree.setUniformRowHeights(True)
        tree.setSelectionBehavior(QTreeWidget.SelectRows)

        header = tree.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)

        tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        tree.itemClicked.connect(
            lambda *_: self._set_active_list(tree)
        )
        return tree

    # -------------------------------------------------- режим
    def vfs_enabled(self):
        return self.vfs_check_box.isChecked()

    def _on_vfs_check_box_toggled(self, checked):
        if not checked:
            self.clear_lost_nodes()
        if self._on_vfs_toggled is not None:
            self._on_vfs_toggled(bool(checked))

    # -------------------------------------------------- наполнение
    def update_graph(self, graph, vfs_mode=False, edge_styles=None,
                     openers=None):
        """Показать итоговый (resolved) граф в левой панели."""
        self._graph = graph
        self._vfs_mode = bool(vfs_mode)
        self._edge_styles = dict(EDGE_STYLES)
        if edge_styles:
            self._edge_styles.update(edge_styles)
        self._openers = dict(openers or {})

        title = "Связи секции"
        if self._vfs_mode:
            title += " (VFS)"
        self.setWindowTitle("%s: %s" % (title, graph.section_name))

        info = (
            "Секция: %s\n"
            "Объявлена в: %s\n"
            "Строка: %s\n"
            "Найдено связей: %d"
            % (
                graph.section_name,
                _node_file_name(graph.root),
                graph.root.first_match_line,
                len(graph.children),
            )
        )
        info += "\nРежим: итоговое дерево"
        self.info_label.setText(info)
        self.info_label.setToolTip(_path_text(graph.root.file_path))

        # Левый список — только итоговые связи. Потерянные сюда
        # не добавляются и отсюда не удаляются.
        self._populate_list(
            self.list_widget,
            graph.children,
            include_root=True,
        )
        self.lost_list_widget.clear()
        self.lost_panel.setVisible(False)
        self._set_active_list(self.list_widget)

    def set_lost_nodes(self, nodes, edge_styles=None, openers=None):
        """Заполнить правую панель, не меняя левый список."""
        if edge_styles:
            self._edge_styles.update(edge_styles)
        if openers is not None:
            self._openers = dict(openers)

        self._populate_list(
            self.lost_list_widget,
            nodes,
            include_root=False,
        )
        self.lost_panel.setVisible(True)
        if not self._lost_panel_expanded:
            self.resize(self.width() + 420, self.height())
            self._lost_panel_expanded = True

        info = self.info_label.text()
        info = "\n".join(
            line for line in info.splitlines()
            if not line.startswith("Потерянных связей:")
        )
        info += "\nПотерянных связей: %d" % len(nodes)
        self.info_label.setText(info)

    def clear_lost_nodes(self):
        """Скрыть правую панель, не трогая левый список."""
        self.lost_list_widget.clear()
        self.lost_panel.setVisible(False)
        info = "\n".join(
            line for line in self.info_label.text().splitlines()
            if not line.startswith("Потерянных связей:")
        )
        self.info_label.setText(info)

    def _populate_list(self, list_widget, nodes, include_root):
        list_widget.clear()

        if include_root:
            list_widget.addTopLevelItem(
                self._make_root_item(self._graph.root)
            )

        for node in nodes:
            list_widget.addTopLevelItem(self._make_item(node))

    def _make_root_item(self, node):
        label, color = _edge_style(
            "declaration",
            self._theme_color,
            self._edge_styles,
        )
        item = QTreeWidgetItem()
        item.setText(0, "[%s] %s" % (label, _node_file_name(node)))
        item.setText(1, str(getattr(node, "matches_count", 1) or 1))
        item.setText(2, str(node.first_match_line))
        if color is not None:
            item.setForeground(0, color)
        item.setData(0, Qt.UserRole, node)
        item.setToolTip(0, _path_text(node.file_path))
        return item

    def _make_item(self, node):
        edge_type = getattr(node, "primary_edge_type", "declaration")
        if edge_type == "inheritance":
            is_parent = self._inheritance_is_parent(node)
            label = "Родитель" if is_parent else "Ребенок"
            _decl_label, color = _edge_style(
                "inheritance",
                self._theme_color,
                self._edge_styles,
            )
        else:
            label, color = _edge_style(
                edge_type,
                self._theme_color,
                self._edge_styles,
            )

        item = QTreeWidgetItem()
        item.setText(0, "[%s] %s" % (label, _node_file_name(node)))
        item.setText(1, str(node.matches_count))
        item.setText(2, str(node.first_match_line))
        if color is not None:
            item.setForeground(0, color)

        item.setData(0, Qt.UserRole, node)
        item.setToolTip(0, self._node_tooltip(node))
        return item

    @staticmethod
    def _inheritance_is_parent(node):
        for match in getattr(node, "matches", ()):
            if getattr(match, "edge_type", "") == "inheritance":
                return bool(
                    getattr(match, "related_is_parent", False)
                )
        return False

    @staticmethod
    def _node_tooltip(node):
        source = "%s:%s" % (
            _path_text(node.file_path),
            getattr(node, "first_match_line", "—"),
        )
        target = getattr(node, "target_path", None)
        physical = getattr(node, "physical_path", None)
        raw_ref = getattr(node, "raw_ref", None)
        related = getattr(node, "related_name", None)

        if (
            getattr(node, "primary_edge_type", "") == "inheritance"
            and related
        ):
            line_text = ""
            if getattr(node, "matches", None):
                line_text = str(node.matches[0].line_text or "").strip()
            role = (
                "Родитель"
                if SectionGraphWindow._inheritance_is_parent(node)
                else "Ребенок"
            )
            parts = ["%s: %s" % (role, related)]
            if line_text:
                parts.append("Запись: %s" % line_text)
            parts.append("Файл: %s" % source)
            related_file = getattr(node, "related_file_path", None)
            if related_file is not None:
                parts.append(
                    "Объявление родителя: %s:%s"
                    % (related_file, getattr(node, "related_line", "—"))
                )
            return "\n".join(parts)

        if target is not None and not getattr(node, "is_asset", False):
            lines = ["Файл: %s" % target]
            fields = sorted({
                match.related
                for match in getattr(node, "matches", ())
                if getattr(match, "related", None)
            })
            if fields:
                lines.append("Поле: %s" % ", ".join(fields))
            if raw_ref:
                lines.append("Ссылка: %s" % raw_ref)
            lines.append("Объявлено: %s" % source)
            return "\n".join(lines)

        if getattr(node, "is_asset", False):
            lines = []
            if target is not None:
                lines.append("Файл (VFS): %s" % target)
            if physical is not None:
                if target is None or (
                    str(physical).lower() != str(target).lower()
                ):
                    label = (
                        "Временная копия (usvfs)"
                        if _is_materialized(physical)
                        else "Реальный путь"
                    )
                    lines.append("%s: %s" % (label, physical))
            if target is None and physical is None:
                lines.append(
                    "Файл не найден по ссылке: %s" % (raw_ref or "—")
                )
            if getattr(node, "is_visual", False):
                lines.append(
                    "Модель .ogf запакована в игру — открытие недоступно."
                )
            lines.append("Объявлено: %s" % source)
            if raw_ref:
                lines.append("Ссылка: %s" % raw_ref)
            return "\n".join(lines)

        return source

    # -------------------------------------------------- действия
    def _set_active_list(self, list_widget):
        self._active_list = list_widget

    def _on_item_double_clicked(self, item):
        node = item.data(0, Qt.UserRole) if item is not None else None
        if node is None:
            return

        # Модуль, добавивший тип связи, сам открывает свои узлы.
        opener = self._openers.get(
            str(getattr(node, "primary_edge_type", ""))
        )
        if opener is not None:
            opener(node)
            return

        # Визуальные модели (.ogf) обычно лежат в игровых архивах и
        # недоступны для внешнего просмотра — оставляем заглушку.
        if getattr(node, "is_visual", False):
            self._notify(
                "Модель .ogf запакована в игру и недоступна для открытия.\n"
                "Ссылка: %s\nОбъявлено в %s, строка %s"
                % (
                    getattr(node, "raw_ref", None) or node.display_name,
                    _path_text(node.file_path),
                    node.first_match_line,
                )
            )
            return

        # Текстуры (.dds) — бинарные ресурсы: открываем приложением
        # Windows. Если ассоциации нет, оболочка покажет диалог
        # «Открыть с помощью…».
        if getattr(node, "is_asset", False):
            target = (
                getattr(node, "physical_path", None)
                or getattr(node, "target_path", None)
            )
            if target is not None:
                if self._open_with_shell is not None:
                    ok, message = self._open_with_shell(target)
                    if not ok:
                        self._notify(message)
                    return
                self._open_node_source(node)
                return

            self._notify(
                "Файл не найден: %s\nОбъявлено в %s, строка %s"
                % (
                    getattr(node, "raw_ref", None) or node.display_name,
                    _path_text(node.file_path),
                    node.first_match_line,
                )
            )
            self._open_node_source(node)
            return

        if node.file_path is None:
            return

        related_path = getattr(node, "related_file_path", None)
        if related_path is not None:
            line_number = int(getattr(node, "related_line", None) or 0)
            resolved = bool(getattr(node, "related_resolved", False))
            if resolved or self._open_raw is None:
                self._open_at_line(related_path, line_number)
            else:
                self._open_raw(related_path, line_number)
            return

        self._open_node_source(node)

    def _open_node_source(self, node):
        """Открыть узел: итоговый через шину, сырой — через raw-view."""
        if node.file_path is None:
            return

        resolved = bool(getattr(node, "resolved", False))
        if resolved or self._open_raw is None:
            self._open_at_line(node.file_path, node.first_match_line)
            return

        self._open_raw(node.file_path, node.first_match_line)

    def _notify(self, message):
        if self._on_message is not None:
            self._on_message(message)

    def _on_open_clicked(self):
        self._on_item_double_clicked(self._active_list.currentItem())


def _node_key(node):
    """Ключ узла: файл + строка + ссылка + target, для сравнения узлов."""
    target = getattr(node, "target_path", None)
    return (
        str(node.file_path) if node.file_path is not None else None,
        getattr(node, "first_match_line", None),
        getattr(node, "raw_ref", None),
        getattr(node, "display_name", None),
        getattr(node, "related_name", None),
        str(target) if target is not None else None,
    )


def _match_key(file_path, match):
    """Семантический ключ текстовой связи raw VFS и resolved-графа.

    Номера строк и имена файлов после резолва меняются, поэтому
    сравниваем саму связь: тип, участника, направление и текст записи.
    """
    return (
        str(getattr(match, "edge_type", "") or ""),
        getattr(match, "related", None),
        bool(getattr(match, "related_is_parent", False)),
        str(getattr(match, "line_text", "") or "").strip(),
    )


def _link_key(node, match):
    """Ключ связи. Для ресурсов сравниваем сам файл, а не строку."""
    edge_type = str(getattr(match, "edge_type", "") or "")
    target = getattr(node, "target_path", None)
    if edge_type in ("texture_reference", "visual_reference") or (
        target is not None
        and edge_type not in ("inheritance", "declaration")
    ):
        if target is not None:
            return (edge_type, _path_key(target))
        raw_ref = str(getattr(node, "raw_ref", "") or "")
        return (
            edge_type,
            raw_ref.replace("/", "\\").strip().lower(),
        )
    return _match_key(None, match)


def _live_graph_state(graph):
    """Ключи связей и XML-файлов, уже попавших в resolved-граф."""
    match_keys = set()
    xml_files = set()
    nodes = [graph.root] + list(graph.children)
    for node in nodes:
        file_path = getattr(node, "file_path", None)
        if file_path is not None:
            if str(file_path).lower().endswith(".xml"):
                xml_files.add(_path_key(file_path))
        for match in getattr(node, "matches", ()):
            match_keys.add(_link_key(node, match))
    return match_keys, xml_files


def _lost_from_raw(raw_graph, live_match_keys, live_xml_files):
    """Оставить в сыром VFS-списке только связи, которых нет в живом графе."""
    lost = []
    for node in raw_graph.children:
        file_path = getattr(node, "file_path", None)
        if (
            file_path is not None
            and str(file_path).lower().endswith(".xml")
            and _path_key(file_path) in live_xml_files
        ):
            continue

        remaining = tuple(
            match
            for match in getattr(node, "matches", ())
            if _link_key(node, match) not in live_match_keys
        )
        if not remaining:
            continue

        lost.append(
            replace(
                node,
                matches=remaining,
                first_match_line=remaining[0].line_number,
                resolved=False,
            )
        )
    return lost


def _unique_lost(nodes, live_node_keys):
    """Добавить в правую панель только то, чего нет в левой."""
    seen = set(live_node_keys)
    result = []
    for node in nodes:
        key = _node_key(node)
        if key in seen:
            continue
        seen.add(key)
        result.append(node)
    return result


def _path_text(path) -> str:
    return str(path) if path is not None else "—"


def _path_name(path) -> str:
    return Path(path).name if path is not None else "—"