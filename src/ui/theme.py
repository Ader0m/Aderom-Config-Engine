# -*- coding: utf-8 -*-
"""Тема оформления.

DARK_THEME реализует утверждённый макет главного окна V2.1
(Aderom Config Engine) в стиле JetBrains Darcula:
  - фон окна/редактора      #2B2B2B
  - фон панелей/сайдбаров   #313335
  - разделители/рамки       #3C3F41
  - акцент (подсветка)      #3592E4 / бирюзовый #4EC9B0
  - основной текст          #BCBFBC, вторичный #787878

Формат соответствует эталонному theme.txt: строка DARK_THEME
с QSS + функция apply_theme(app).
"""

DARK_THEME = """
/* ============ Базовые цвета ============
   bg-main:      #2B2B2B
   bg-panel:     #313335
   bg-titlebar:  #3C3F41
   border:       #3C3F41
   accent:       #3592E4
   accent-teal:  #4EC9B0
   selection:    #2E436E
   text:         #BCBFBC
   text-muted:   #787878
   text-bright:  #FFFFFF
   ==================================== */

QMainWindow {
    background-color: #2B2B2B;
    color: #BCBFBC;
}

QWidget {
    background-color: transparent;
    color: #BCBFBC;
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 13px;
    selection-background-color: #2E436E;
    selection-color: #FFFFFF;
}

/* ============ Меню ============ */

QMenuBar {
    background-color: #3C3F41;
    color: #BCBFBC;
    border-bottom: 1px solid #313335;
    padding: 2px;
    spacing: 2px;
}

QMenuBar::item {
    background: transparent;
    padding: 4px 10px;
    border-radius: 4px;
}

QMenuBar::item:selected {
    background-color: #4E5254;
    color: #FFFFFF;
}

QMenuBar::item:pressed {
    background-color: #3592E4;
    color: #FFFFFF;
}

QMenu {
    background-color: #313335;
    color: #BCBFBC;
    border: 1px solid #4E5254;
    border-radius: 6px;
    padding: 4px;
}

QMenu::item {
    padding: 5px 28px 5px 20px;
    border-radius: 4px;
}

QMenu::item:selected {
    background-color: #2E436E;
    color: #FFFFFF;
}

QMenu::item:disabled {
    color: #5A5D63;
}

QMenu::separator {
    height: 1px;
    background: #3C3F41;
    margin: 4px 8px;
}

QMenu::indicator {
    width: 14px;
    height: 14px;
}

/* ============ Панели инструментов ============ */

QToolBar {
    background-color: #3C3F41;
    border: none;
    border-bottom: 1px solid #313335;
    padding: 3px;
    spacing: 4px;
}

QToolBar::separator {
    width: 1px;
    background: #4E5254;
    margin: 4px 6px;
}

QToolButton {
    background: transparent;
    color: #BCBFBC;
    border: none;
    border-radius: 4px;
    padding: 4px 6px;
}

QToolButton:hover {
    background-color: #4E5254;
    color: #FFFFFF;
}

QToolButton:pressed, QToolButton:checked {
    background-color: #2E436E;
    color: #FFFFFF;
}

QToolButton:disabled {
    color: #5A5D63;
}

/* ============ Кнопки ============ */

QPushButton {
    background-color: #3C3F41;
    color: #BCBFBC;
    border: 1px solid #4E5254;
    border-radius: 6px;
    padding: 5px 16px;
    min-height: 18px;
}

QPushButton:hover {
    background-color: #4E5254;
    color: #FFFFFF;
    border-color: #5A5D63;
}

QPushButton:pressed {
    background-color: #313335;
}

QPushButton:default {
    background-color: #3592E4;
    border-color: #3592E4;
    color: #FFFFFF;
}

QPushButton:default:hover {
    background-color: #4C9FE8;
    border-color: #4C9FE8;
}

QPushButton:disabled {
    background-color: #313335;
    color: #5A5D63;
    border-color: #3C3F41;
}

/* ============ Док-панели (Project Tree, Dependency Graph, консоль) ============ */

QDockWidget {
    color: #BCBFBC;
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
}

QDockWidget::title {
    background-color: #313335;
    color: #A9B7C6;
    padding: 6px 10px;
    border-bottom: 1px solid #3C3F41;
    text-align: left;
}

QDockWidget::close-button, QDockWidget::float-button {
    background: transparent;
    border: none;
    border-radius: 3px;
    padding: 2px;
}

QDockWidget::close-button:hover, QDockWidget::float-button:hover {
    background-color: #4E5254;
}

/* ============ Дерево файлов ============ */

QTreeWidget, QTreeView {
    background-color: #313335;
    alternate-background-color: #2E3033;
    color: #BCBFBC;
    border: none;
    padding: 2px;
}

QTreeWidget::item, QTreeView::item {
    height: 24px;
    padding-left: 4px;
    border: none;
    border-radius: 4px;
}

QTreeWidget::item:hover, QTreeView::item:hover {
    background-color: #3A3D41;
}

QTreeWidget::item:selected, QTreeView::item:selected {
    background-color: #2E436E;
    color: #FFFFFF;
}

QTreeWidget::item:selected:active, QTreeView::item:selected:active {
    background-color: #2E436E;
    color: #FFFFFF;
}

/* ============ Ветки дерева (стрелки раскрытия) ============ */
QTreeWidget::branch, QTreeView::branch {
    background: transparent;
}

QTreeWidget::branch:has-children:closed, QTreeView::branch:has-children:closed {
    image: url("@BRANCH_CLOSED@");
}

QTreeWidget::branch:has-children:open, QTreeView::branch:has-children:open {
    image: url("@BRANCH_OPEN@");
}

/* ============ Редактор кода ============ */

QTextEdit, QPlainTextEdit {
    background-color: #2B2B2B;
    color: #A9B7C6;
    border: none;
    selection-background-color: #2E436E;
    selection-color: #FFFFFF;
    font-family: "JetBrains Mono", "Cascadia Code", "Consolas", monospace;
    font-size: 13px;
    padding: 4px;
}

/* Подсветка синтаксиса задаётся в коде приложения (QSyntaxHighlighter):
   секции [section]  -> #C578DD (фиолетовый)
   ключи             -> #9CDCFE (светло-голубой)
   значения          -> #B5CEA8 (салатовый)
   комментарии       -> #6A9955 (серо-зелёный)
   строка-override   -> фон #2E436E */

/* ============ Вкладки редактора ============ */

QTabWidget::pane {
    border: none;
    border-top: 1px solid #3C3F41;
    background-color: #2B2B2B;
}

QTabBar {
    background-color: #313335;
    qproperty-drawBase: false;
}

QTabBar::tab {
    background-color: #313335;
    color: #787878;
    border: none;
    border-right: 1px solid #3C3F41;
    padding: 7px 16px;
    min-width: 80px;
}

QTabBar::tab:hover {
    background-color: #3A3D41;
    color: #BCBFBC;
}

QTabBar::tab:selected {
    background-color: #2B2B2B;
    color: #FFFFFF;
    border-bottom: 2px solid #3592E4;
}

QTabBar::tab:!selected {
    margin-top: 0px;
}

QTabBar::close-button {
    subcontrol-position: right;
    background: transparent;
    border-radius: 3px;
}

QTabBar::close-button:hover {
    background-color: #4E5254;
}

/* ============ Нижняя консоль (единая панель во всю ширину) ============ */

QTabWidget#bottomPanel QTabBar::tab:selected {
    border-bottom: 2px solid #4EC9B0;
}

/* Цвета уровней лога задаются в коде:
   INFO  -> #4EC9B0 (бирюзовый)
   WARN  -> #CCA700 (янтарный)
   ERROR -> #F14C4C (красный) */

/* ============ Строка состояния ============ */

QStatusBar {
    background-color: #313335;
    color: #A9B7C6;
    border-top: 1px solid #3C3F41;
    padding: 2px 8px;
    font-size: 12px;
}

QStatusBar::item {
    border: none;
}

QStatusBar QLabel {
    color: #A9B7C6;
    padding: 0px 6px;
    background: transparent;
}

/* ============ Полосы прокрутки ============ */

QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 0px;
    border: none;
}

QScrollBar::handle:vertical {
    background: #424242;
    min-height: 30px;
    border-radius: 5px;
    margin: 2px;
}

QScrollBar::handle:vertical:hover {
    background: #4E4E4E;
}

QScrollBar::handle:vertical:pressed {
    background: #3592E4;
}

QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 0px;
    border: none;
}

QScrollBar::handle:horizontal {
    background: #424242;
    min-width: 30px;
    border-radius: 5px;
    margin: 2px;
}

QScrollBar::handle:horizontal:hover {
    background: #4E4E4E;
}

QScrollBar::handle:horizontal:pressed {
    background: #3592E4;
}

QScrollBar::add-line, QScrollBar::sub-line {
    width: 0px;
    height: 0px;
    background: none;
    border: none;
}

QScrollBar::add-page, QScrollBar::sub-page {
    background: transparent;
}

/* ============ Сплиттеры ============ */

QSplitter::handle {
    background-color: #3C3F41;
}

QSplitter::handle:horizontal {
    width: 1px;
}

QSplitter::handle:vertical {
    height: 1px;
}

QSplitter::handle:hover {
    background-color: #3592E4;
}

/* ============ Группы и метки ============ */

QGroupBox {
    background-color: #313335;
    border: 1px solid #3C3F41;
    border-radius: 6px;
    margin-top: 12px;
    padding: 10px;
    font-weight: 600;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0px 4px;
    color: #A9B7C6;
}

QLabel {
    background: transparent;
    color: #BCBFBC;
    border: none;
}

QLabel[role="muted"] {
    color: #787878;
}

QLabel[role="accent"] {
    color: #4EC9B0;
}

/* ============ Поля ввода ============ */

QLineEdit {
    background-color: #2B2B2B;
    color: #BCBFBC;
    border: 1px solid #3C3F41;
    border-radius: 4px;
    padding: 4px 8px;
    selection-background-color: #2E436E;
    selection-color: #FFFFFF;
}

QLineEdit:hover {
    border-color: #4E5254;
}

QLineEdit:focus {
    border-color: #3592E4;
}

QLineEdit:disabled {
    background-color: #313335;
    color: #5A5D63;
}

QLineEdit[role="search"] {
    background-color: #3C3F41;
    border-radius: 10px;
    padding: 4px 12px;
}

/* ============ Выпадающие списки ============ */

QComboBox {
    background-color: #3C3F41;
    color: #BCBFBC;
    border: 1px solid #4E5254;
    border-radius: 4px;
    padding: 4px 8px;
    min-height: 18px;
}

QComboBox:hover {
    border-color: #5A5D63;
}

QComboBox:focus {
    border-color: #3592E4;
}

QComboBox::drop-down {
    border: none;
    width: 20px;
}

QComboBox QAbstractItemView {
    background-color: #313335;
    color: #BCBFBC;
    border: 1px solid #4E5254;
    border-radius: 4px;
    selection-background-color: #2E436E;
    selection-color: #FFFFFF;
    padding: 2px;
}

/* ============ Чекбоксы и радиокнопки ============ */

QCheckBox {
    background: transparent;
    color: #BCBFBC;
    spacing: 8px;
}

QCheckBox::indicator {
    width: 15px;
    height: 15px;
    border: 1px solid #4E5254;
    border-radius: 4px;
    background-color: #2B2B2B;
}

QCheckBox::indicator:hover {
    border-color: #3592E4;
}

QCheckBox::indicator:checked {
    background-color: #3592E4;
    border-color: #3592E4;
}

QCheckBox::indicator:disabled {
    background-color: #313335;
    border-color: #3C3F41;
}

QRadioButton {
    background: transparent;
    color: #BCBFBC;
    spacing: 8px;
}

QRadioButton::indicator {
    width: 15px;
    height: 15px;
    border: 1px solid #4E5254;
    border-radius: 8px;
    background-color: #2B2B2B;
}

QRadioButton::indicator:hover {
    border-color: #3592E4;
}

QRadioButton::indicator:checked {
    background-color: #3592E4;
    border-color: #3592E4;
}

/* ============ Таблицы (Patch Manager, Value Sources) ============ */

QTableView, QTableWidget {
    background-color: #2B2B2B;
    alternate-background-color: #303235;
    color: #BCBFBC;
    border: none;
    gridline-color: #3C3F41;
    selection-background-color: #2E436E;
    selection-color: #FFFFFF;
}

QTableView::item {
    padding: 4px 8px;
    border: none;
}

QTableView::item:selected {
    background-color: #2E436E;
    color: #FFFFFF;
}

QHeaderView {
    background-color: #313335;
    border: none;
    border-bottom: 1px solid #3C3F41;
}

QHeaderView::section {
    background-color: #313335;
    color: #A9B7C6;
    border: none;
    border-right: 1px solid #3C3F41;
    padding: 6px 8px;
    font-weight: 600;
}

QHeaderView::section:hover {
    background-color: #3A3D41;
    color: #FFFFFF;
}

/* ============ Списки (Modules / plugin browser) ============ */

QListView, QListWidget {
    background-color: #313335;
    color: #BCBFBC;
    border: none;
    padding: 2px;
}

QListView::item {
    padding: 6px 8px;
    border-radius: 4px;
}

QListView::item:hover {
    background-color: #3A3D41;
}

QListView::item:selected {
    background-color: #2E436E;
    color: #FFFFFF;
}

/* ============ Подсказки ============ */

QToolTip {
    background-color: #313335;
    color: #BCBFBC;
    border: 1px solid #4E5254;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 12px;
}

/* ============ Прогресс и ползунки ============ */

QProgressBar {
    background-color: #313335;
    border: none;
    border-radius: 4px;
    height: 8px;
    text-align: center;
    color: transparent;
}

QProgressBar::chunk {
    background-color: #3592E4;
    border-radius: 4px;
}

QSlider::groove:horizontal {
    background: #3C3F41;
    height: 4px;
    border-radius: 2px;
}

QSlider::handle:horizontal {
    background: #BCBFBC;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}

QSlider::handle:horizontal:hover {
    background: #3592E4;
}

QSlider::sub-page:horizontal {
    background: #3592E4;
    border-radius: 2px;
}

/* ============ Диалоги ============ */

QDialog {
    background-color: #2B2B2B;
}

QMessageBox {
    background-color: #2B2B2B;
}

/* ============ Активная полоса вкладки окна (опционально) ============ */

QMainWindow::separator {
    background-color: #3C3F41;
    width: 1px;
    height: 1px;
}

QMainWindow::separator:hover {
    background-color: #3592E4;
}
"""


def apply_theme(app):
    """Применяет тёмную тему V2.1 ко всему приложению PyQt5."""
    app.setStyleSheet(DARK_THEME)

import os
import tempfile

from PyQt5.QtCore import Qt, QPoint
from PyQt5.QtGui import QColor, QImage, QPainter, QPolygon

_BRANCH_DIR = os.path.join(tempfile.gettempdir(), "ace_theme_icons")


def _draw_triangle(path, points):
    img = QImage(12, 12, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#BCBFBC"))          # основной цвет текста темы
    p.drawPolygon(QPolygon([QPoint(*pt) for pt in points]))
    p.end()
    img.save(path)
    return path.replace("\\", "/")         # QSS url() любит прямые слеши


def ensure_branch_icons():
    os.makedirs(_BRANCH_DIR, exist_ok=True)
    closed = _draw_triangle(
        os.path.join(_BRANCH_DIR, "branch_closed.png"),
        [(4, 2), (4, 10), (10, 6)],        # ▶
    )
    opened = _draw_triangle(
        os.path.join(_BRANCH_DIR, "branch_open.png"),
        [(2, 4), (10, 4), (6, 10)],        # ▼
    )
    return closed, opened


def apply_theme(app):
    """Применяет тёмную тему V2.1 ко всему приложению PyQt5."""
    closed, opened = ensure_branch_icons()
    app.setStyleSheet(
        DARK_THEME
        .replace("@BRANCH_CLOSED@", closed)
        .replace("@BRANCH_OPEN@", opened)
    )