# -*- coding: utf-8 -*-
"""
Виджет графа связей секции — «звезда» вокруг центральной секции.

Отображает:
- центр (искомая секция)
- файловые узлы (где найдены упоминания)
- секции наследования (parent/child)
- рёбра с цветами по силе связи
- предупреждения (циклы, висячие ссылки)
"""

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QBrush
from PyQt5.QtWidgets import (
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QLabel,
    QMessageBox,
)

from core.reference_graph import (
    GraphSnapshot,
    GraphDiagnostic,
    EdgeLabel,
    NodeType,
    Strength,
)


# Цвета для силы связи (решение 1: ребро маркируется цветом)
STRENGTH_COLORS = {
    Strength.HIGH: QColor("#4CAF50"),    # зелёный
    Strength.MEDIUM: QColor("#FF9800"),  # оранжевый
    Strength.LOW: QColor("#9E9E9E"),     # серый
}

# Цвета для типов связей
EDGE_COLORS = {
    EdgeLabel.PARENT: QColor("#2196F3"),   # синий (наследование)
    EdgeLabel.CHILD: QColor("#2196F3"),
    EdgeLabel.HIGH: STRENGTH_COLORS[Strength.HIGH],
    EdgeLabel.MEDIUM: STRENGTH_COLORS[Strength.MEDIUM],
    EdgeLabel.LOW: STRENGTH_COLORS[Strength.LOW],
    EdgeLabel.XML: QColor("#9C27B0"),      # фиолетовый
    EdgeLabel.IMAGE: QColor("#00BCD4"),    # голубой
    EdgeLabel.SCRIPT: QColor("#FF5722"),   # красно-оранжевый
}

NODE_TYPE_ICONS = {
    NodeType.SECTION: "📦",
    NodeType.LTX_FILE: "📄",
    NodeType.XML_FILE: "📋",
    NodeType.SCRIPT: "⚙️",
    NodeType.RESOURCE: "🖼️",
}


class ReferenceGraphWidget(QWidget):
    """Виджет для отображения графа связей секции."""

    file_open_requested = pyqtSignal(str)  # путь к файлу для открытия
    section_selected = pyqtSignal(str)      # имя секции для навигации

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()
        self._graph_result = None

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Заголовок
        self.title_label = QLabel("Граф связей: (не выбрано)")
        self.title_label.setStyleSheet("font-weight: bold; font-size: 12px;")
        layout.addWidget(self.title_label)

        # Дерево графа
        self.graph_tree = QTreeWidget()
        self.graph_tree.setHeaderLabels(["Узел", "Тип", "Сила", "Упоминаний"])
        self.graph_tree.setColumnWidth(0, 400)
        self.graph_tree.setColumnWidth(1, 120)
        self.graph_tree.setColumnWidth(2, 80)

        self.graph_tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.graph_tree.itemExpanded.connect(self._on_item_expanded)

        layout.addWidget(self.graph_tree, 1)

        # Статус
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #666; font-size: 10px;")
        layout.addWidget(self.status_label)

    def display_graph(self, section_name: str, result):
        """Отображает результат построения графа.

        Args:
            section_name: имя секции
            result: GraphSnapshot или GraphDiagnostic
        """
        self._graph_result = result
        self.graph_tree.clear()

        if isinstance(result, GraphDiagnostic):
            self.title_label.setText(f"Граф связей: [{section_name}] — диагностика")
            self.status_label.setText(result.message)

            # Показываем диагностический узел
            diag_item = QTreeWidgetItem(
                self.graph_tree,
                [f"⚠️ {section_name}", "Диагностика", "—", "—"],
            )
            diag_item.setForeground(0, QColor("#F44336"))
            diag_item.setData(0, Qt.UserRole, {"type": "diagnostic"})

            if result.detail:
                detail_item = QTreeWidgetItem(diag_item, [result.detail.value, "", "", ""])
                detail_item.setForeground(0, QColor("#999"))

            return

        # GraphSnapshot
        snapshot: GraphSnapshot = result
        self.title_label.setText(f"Граф связей: [{section_name}]")
        self.status_label.setText(
            f"{snapshot.node_count} узлов, {snapshot.edge_count} рёбер, "
            f"{len(snapshot.files)} файлов"
        )

        # Центральный узел
        center_node = snapshot.nodes[snapshot.center_id]
        center_item = self._create_node_item(center_node, snapshot)
        self.graph_tree.addTopLevelItem(center_item)
        center_item.setExpanded(True)

        # Рёбра (дети центрального узла)
        for edge in snapshot.edges:
            if edge.source == snapshot.center_id:
                # Исходящее ребро от центра
                target_node = snapshot.nodes[edge.target]
                edge_item = self._create_edge_item(edge, target_node, snapshot)
                center_item.addChild(edge_item)

        # Входящие рёбра (кто ссылается на центр)
        incoming_edges = [e for e in snapshot.edges if e.target == snapshot.center_id]
        if incoming_edges:
            refs_group = QTreeWidgetItem(center_item, ["📎 Ссылки на эту секцию", "", "", ""])
            refs_group.setExpanded(False)
            refs_group.setForeground(0, QColor("#666"))

            for edge in incoming_edges:
                source_node = snapshot.nodes[edge.source]
                edge_item = self._create_edge_item(edge, source_node, snapshot)
                refs_group.addChild(edge_item)

        # Предупреждения
        if snapshot.warnings:
            warn_group = QTreeWidgetItem(
                self.graph_tree,
                [f"⚠️ Предупреждения ({len(snapshot.warnings)})", "", "", ""],
            )
            warn_group.setForeground(0, QColor("#FF9800"))

            for warning in snapshot.warnings:
                warn_item = QTreeWidgetItem(warn_group, [warning, "", "", ""])
                warn_item.setForeground(0, QColor("#FF9800"))

    def _create_node_item(self, node, snapshot: GraphSnapshot):
        """Создаёт элемент дерева для узла."""
        icon = NODE_TYPE_ICONS.get(node.type, "❓")
        node_type = node.type.value

        item = QTreeWidgetItem(
            [f"{icon} {node.name}", node_type, "", ""],
        )

        # Имя узла
        item.setData(0, Qt.UserRole, {"type": "node", "node_id": node.id, "node": node})

        # Цвет фона по типу
        if node.type == NodeType.SECTION:
            item.setBackground(0, QColor("#E3F2FD"))  # светло-синий

        return item

    def _create_edge_item(self, edge, target_node, snapshot: GraphSnapshot):
        """Создаёт элемент дерева для ребра."""
        icon = NODE_TYPE_ICONS.get(target_node.type, "❓")
        label = edge.label.value
        strength = edge.strength.name
        mention_count = len(edge.mentions)

        item = QTreeWidgetItem(
            [f"{icon} {target_node.name}", label, strength, str(mention_count)],
        )

        # Цвет по типу ребра
        color = EDGE_COLORS.get(edge.label, QColor("#999"))
        item.setForeground(0, color)

        # Сохраняем данные
        item.setData(0, Qt.UserRole, {
            "type": "edge",
            "edge": edge,
            "target_node": target_node,
        })

        # Добавляем упоминания как детей
        if edge.mentions:
            item.setExpanded(False)  # свернуть по умолчанию
            for mention in edge.mentions:
                mention_text = self._format_mention(mention)
                mention_item = QTreeWidgetItem(
                    item,
                    [mention_text, mention.origin.value, "", ""],
                )
                mention_item.setForeground(0, QColor("#888"))
                mention_item.setData(0, Qt.UserRole, {
                    "type": "mention",
                    "mention": mention,
                })

        return item

    def _format_mention(self, mention):
        """Форматирует упоминание для отображения."""
        parts = []
        if mention.user_section:
            parts.append(f"[{mention.user_section}]")
        if mention.key:
            parts.append(f"{mention.key} =")
        parts.append(mention.tree_file)
        return " ".join(parts)

    def _on_item_double_clicked(self, item, column):
        """Обработка двойного клика по элементу."""
        data = item.data(0, Qt.UserRole)
        if not data:
            return

        item_type = data.get("type")

        if item_type == "node":
            node = data.get("node")
            if node and node.type == NodeType.SECTION:
                self.section_selected.emit(node.name)

        elif item_type == "edge":
            target_node = data.get("target_node")
            if target_node and target_node.type in (NodeType.LTX_FILE, NodeType.XML_FILE, NodeType.SCRIPT):
                # Открыть файл
                self.file_open_requested.emit(target_node.path)

        elif item_type == "mention":
            mention = data.get("mention")
            if mention:
                # Открыть файл из упоминания
                self.file_open_requested.emit(mention.tree_file)

    def _on_item_expanded(self, item):
        """Ленивая загрузка детей (если нужно)."""
        pass  # В v1 все дети загружаются сразу

    def clear(self):
        """Очищает виджет."""
        self.graph_tree.clear()
        self.title_label.setText("Граф связей: (не выбрано)")
        self.status_label.setText("")
        self._graph_result = None

    def get_current_graph(self):
        """Возвращает текущий результат графа."""
        return self._graph_result