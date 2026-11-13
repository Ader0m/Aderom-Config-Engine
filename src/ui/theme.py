# -*- coding: utf-8 -*-
"""Тема оформления Aderom Config Engine (макет V2.1, JetBrains Darcula).

ЕДИНСТВЕННЫЙ источник всех цветов и шрифтов приложения — палитра Palette.
Из неё создаются:
  1) QSS (DARK_THEME) для обычных Qt-виджетов;
  2) программные цвета QScintilla: ui/editor.py берёт значения из PALETTE
     напрямую, т.к. QSS не управляет стилями лексера Scintilla;
  3) растровые стрелки ветвей дерева (ensure_branch_icons), т.к. QSS
     image: url() умеет ссылаться только на реальные файлы.

Правила слоя темы:
  - Никаких hex-литералов вне Palette (иконки рисуются цветом p.text).
  - Размеры шрифтов в QSS указываются ТОЛЬКО в pt. Пиксельные размеры
    (font-size: 13px) дают QFont с pointSize() == -1; QScintilla передаёт
    pointSizeF() в Scintilla (SCI_STYLESETSIZEFRACTIONAL), и отрицательный
    размер превращает текст редактора в микроскопический (баг коммита 61fc3af).
  - Светлые «полосы» и бирюза в редакторе — штатные per-style дефолты
    QsciLexerProperties (#E0F0F0 у Section, #007F7F у Comment); они
    перебиваются только setFont/setColor/setPaper по всем стилям в editor.py.

Совместимость: модуль по-прежнему экспортирует DARK_THEME (строку QSS с
уже подставленными url иконок) и apply_theme(app); ensure_branch_icons()
возвращает (closed, opened) как раньше.
"""

import os
import tempfile
from dataclasses import asdict, dataclass, replace
from string import Template
from typing import Optional

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QColor, QImage, QPainter, QPolygon, QPen


# ======================================================================
# Палитра — единый источник правды для цветов и шрифтов
# ======================================================================

@dataclass(frozen=True)
class Palette:
    """Палитра темы. Все цвета — строки '#RRGGBB', все размеры шрифтов — pt."""

    # ============ Базовые фоны ============
    bg_main: str = "#2B2B2B"        # окно, редактор
    bg_panel: str = "#313335"       # сайдбары, доки, меню, комбобоксы
    bg_panel_alt: str = "#2E3033"   # alternate-строки дерева
    bg_titlebar: str = "#3C3F41"    # menubar, toolbar, заголовки
    bg_table_alt: str = "#303235"   # alternate-строки таблиц
    tab_bg: str = "#313335"         # вкладки редактора
    tab_bg_selected: str = "#2B2B2B"
    tab_text: str = "#787878"
    tab_text_selected: str = "#FFFFFF"

    # ============ Рамки / разделители ============
    border: str = "#3C3F41"
    border_hover: str = "#4E5254"

    # ============ Акценты ============
    accent: str = "#3592E4"         # синий (фокус, pressed, scroll)
    accent_hover: str = "#4C9FE8"
    accent_teal: str = "#4EC9B0"    # INFO, активная вкладка нижней панели
    accent_amber: str = "#CCA700"   # WARN
    accent_red: str = "#F14C4C"     # ERROR

    # ============ Выделение / hover ============
    selection: str = "#2E436E"
    hover: str = "#3A3D41"
    pressed: str = "#313335"
    scroll_handle: str = "#424242"
    scroll_handle_hover: str = "#4E4E4E"

    # ============ Текст ============
    text: str = "#BCBFBC"
    text_muted: str = "#787878"
    text_bright: str = "#FFFFFF"
    text_dock: str = "#A9B7C6"
    text_disabled: str = "#5A5D63"

    # ============ Подсветка синтаксиса (LTX / XML / Lua, ui/lexers.py) ============
    syn_default: str = "#BCBFBC"    # обычный текст
    syn_section: str = "#C578DD"    # [section], <tag>
    syn_key: str = "#9CDCFE"        # ключи, атрибуты
    syn_value: str = "#B5CEA8"      # значения, строки
    syn_comment: str = "#6A9955"    # комментарии, <!-- -->
    syn_assign: str = "#D4D4D4"     # знак '=', пунктуация
    syn_number: str = "#6897BB"     # числа (Darcula: Number)
    syn_constant: str = "#CC7832"   # true/false/nil, сущности XML
    syn_directive: str = "#CC7832"  # #include, ключевые слова Lua
    syn_include: str = "#C586C0"    # LTX: #include
    syn_override: str = "#FFC66D"   # DLTX: ![section]
    syn_deleted: str = "#F14C4C"    # DLTX: !![section], !key, !parent
    syn_safe: str = "#4EC9B0"       # DLTX: @[section]
    syn_parent: str = "#DCDCAA"     # родители после ]:
    syn_list_add: str = "#4EC9B0"   # DLTX: >key (добавить в CSV)
    syn_list_remove: str = "#CE9178" # DLTX: <key (удалить из CSV)
    syn_list_value: str = "#9ACD8B" # элементы списка через запятую

    # ============ Граф связей секции (modules/base_module/section_graph.py) ============
    # Семантические цвета маркеров типов связей. Значения совпадают с
    # подсветкой LTX, но заданы отдельными полями, чтобы окно графа не
    # зависело от стилей редактора.
    graph_declaration: str = "#C578DD"  # [Объявление]
    graph_inheritance: str = "#DCDCAA"  # [Наследование]
    graph_ltx: str = "#9CDCFE"          # [LTX]
    graph_script: str = "#CC7832"       # [Скрипт]
    graph_xml: str = "#B5CEA8"          # [XML]
    graph_texture: str = "#FFC66D"      # [Текстура]
    graph_visual: str = "#4EC9B0"       # [Модель]
    graph_sound: str = "#9ACD8B"        # [Звук]

    # ============ Редактор (Scintilla, программные цвета) ============
    ed_margin_bg: str = "#313335"
    ed_margin_fg: str = "#787878"
    ed_caret: str = "#BCBFBC"
    ed_caret_line: str = "#2A2D2E"

    # ============ Шрифты (только pt!) ============
    ui_font_family: str = '"Segoe UI", "Inter", sans-serif'
    ui_font_size_pt: int = 10            # ~13px @ 96 DPI
    ui_font_size_small_pt: int = 9       # ~12px: статус-бар, тултипы
    mono_font_family: str = '"JetBrains Mono", "Cascadia Code", "Consolas", monospace'
    editor_font_family: str = "Cascadia Code"
    editor_font_size_pt: int = 10


PALETTE = Palette()


# ======================================================================
# Стрелки ветвей дерева (▶ / ▼)
# ======================================================================

_BRANCH_DIR = os.path.join(tempfile.gettempdir(), "ace_theme_icons")


def _draw_triangle(path, points, color):
    img = QImage(12, 12, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawPolygon(QPolygon([QPoint(*pt) for pt in points]))
    p.end()
    img.save(path)
    return path.replace("\\", "/")         # QSS url() любит прямые слеши

def _draw_cross(path, color, size=16, thickness=2):
    """Рисует аккуратный крестик (X) со скругленными концами."""
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    
    pen = QPen(QColor(color))
    pen.setWidth(thickness)
    pen.setCapStyle(Qt.RoundCap)  # Скругленные углы линий
    p.setPen(pen)
    
    margin = 4
    p.drawLine(margin, margin, size - margin, size - margin)
    p.drawLine(margin, size - margin, size - margin, margin)
    
    p.end()
    img.save(path)
    return path.replace("\\", "/")

def ensure_branch_icons(palette: Optional[Palette] = None):
    """Рисует стрелки ▶/▼ для ветвей дерева во временный каталог.

    QSS image: url() требует реальных файлов, а бинарных ресурсов у темы
    нет, поэтому стрелки генерируются рантаймом цветом основного текста
    палитры. Контракт сохранён: возвращает (closed, opened).
    """
    p = palette if palette is not None else PALETTE
    os.makedirs(_BRANCH_DIR, exist_ok=True)
    closed = _draw_triangle(
        os.path.join(_BRANCH_DIR, "branch_closed.png"),
        [(4, 2), (4, 10), (10, 6)],        # ▶
        p.text,
    )
    opened = _draw_triangle(
        os.path.join(_BRANCH_DIR, "branch_open.png"),
        [(2, 4), (10, 4), (6, 10)],        # ▼
        p.text,
    )
    return closed, opened

def ensure_tab_close_icons(palette: Optional[Palette] = None):
    """Рисует крестик закрытия вкладки для состояний: normal, hover, pressed."""
    p = palette if palette is not None else PALETTE
    os.makedirs(_BRANCH_DIR, exist_ok=True)
    
    normal = _draw_cross(
        os.path.join(_BRANCH_DIR, "tab_close.png"),
        p.text_muted,
    )
    hover = _draw_cross(
        os.path.join(_BRANCH_DIR, "tab_close_hover.png"),
        p.text_bright,
    )
    pressed = _draw_cross(
        os.path.join(_BRANCH_DIR, "tab_close_pressed.png"),
        p.accent_red,  # Красный крестик при нажатии
    )
    return normal, hover, pressed

# ======================================================================
# QSS-шаблон (все значения — из Palette, все шрифты — в pt)
# ======================================================================

_QSS = Template("""
/* ============ Базовые цвета ============
   bg-main:      $bg_main
   bg-panel:     $bg_panel
   bg-titlebar:  $bg_titlebar
   border:       $border
   accent:       $accent
   accent-teal:  $accent_teal
   selection:    $selection
   text:         $text
   text-muted:   $text_muted
   text-bright:  $text_bright
   ==================================== */

QMainWindow {
    background-color: $bg_main;
    color: $text;
}

QWidget {
    background-color: transparent;
    color: $text;
    font-family: $ui_font_family;
    font-size: ${ui_font_size_pt}pt;
    selection-background-color: $selection;
    selection-color: $text_bright;
}

/* ============ Меню ============ */

QMenuBar {
    background-color: $bg_titlebar;
    color: $text;
    border-bottom: 1px solid $bg_panel;
    padding: 2px;
    spacing: 2px;
}

QMenuBar::item {
    background: transparent;
    padding: 4px 10px;
    border-radius: 4px;
}

QMenuBar::item:selected {
    background-color: $border_hover;
    color: $text_bright;
}

QMenuBar::item:pressed {
    background-color: $accent;
    color: $text_bright;
}

QMenu {
    background-color: $bg_panel;
    color: $text;
    border: 1px solid $border_hover;
    border-radius: 6px;
    padding: 4px;
}

QMenu::item {
    padding: 5px 28px 5px 20px;
    border-radius: 4px;
}

QMenu::item:selected {
    background-color: $selection;
    color: $text_bright;
}

QMenu::item:disabled {
    color: $text_disabled;
}

QMenu::separator {
    height: 1px;
    background: $border;
    margin: 4px 8px;
}

QMenu::indicator {
    width: 14px;
    height: 14px;
}

/* ============ Панели инструментов ============ */

QToolBar {
    background-color: $bg_titlebar;
    border: none;
    border-bottom: 1px solid $bg_panel;
    padding: 3px;
    spacing: 4px;
}

QToolBar::separator {
    width: 1px;
    background: $border_hover;
    margin: 4px 6px;
}

QToolButton {
    background: transparent;
    color: $text;
    border: none;
    border-radius: 4px;
    padding: 4px 6px;
}

QToolButton:hover {
    background-color: $border_hover;
    color: $text_bright;
}

QToolButton:pressed, QToolButton:checked {
    background-color: $selection;
    color: $text_bright;
}

QToolButton:disabled {
    color: $text_disabled;
}

/* ============ Кнопки ============ */

QPushButton {
    background-color: $bg_titlebar;
    color: $text;
    border: 1px solid $border_hover;
    border-radius: 6px;
    padding: 5px 16px;
    min-height: 18px;
}

QPushButton:hover {
    background-color: $border_hover;
    color: $text_bright;
    border-color: $border_hover;
}

QPushButton:pressed {
    background-color: $pressed;
}

QPushButton:default {
    background-color: $accent;
    border-color: $accent;
    color: $text_bright;
}

QPushButton:default:hover {
    background-color: $accent_hover;
    border-color: $accent_hover;
}

QPushButton:disabled {
    background-color: $bg_panel;
    color: $text_disabled;
    border-color: $border;
}

/* ============ Док-панели (Project Tree, Dependency Graph, консоль) ============ */

QDockWidget {
    color: $text;
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
}

QDockWidget::title {
    background-color: $bg_panel;
    color: $text_dock;
    padding: 6px 10px;
    border-bottom: 1px solid $border;
    text-align: left;
}

QDockWidget::close-button, QDockWidget::float-button {
    background: transparent;
    border: none;
    border-radius: 3px;
    padding: 2px;
}

QDockWidget::close-button:hover, QDockWidget::float-button:hover {
    background-color: $border_hover;
}

/* ============ Дерево файлов ============ */

QTreeWidget, QTreeView {
    background-color: $bg_panel;
    alternate-background-color: $bg_panel_alt;
    color: $text;
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
    background-color: $hover;
}

QTreeWidget::item:selected, QTreeView::item:selected {
    background-color: $selection;
    color: $text_bright;
}

QTreeWidget::item:selected:active, QTreeView::item:selected:active {
    background-color: $selection;
    color: $text_bright;
}

/* ============ Ветки дерева (стрелки раскрытия) ============
   Иконки рисуются рантаймом (ensure_branch_icons), url подставляются
   при сборке QSS. Кавычки обязательны: в temp-пути бывают пробелы. */

QTreeWidget::branch, QTreeView::branch {
    background: transparent;
}

QTreeWidget::branch:has-children:closed, QTreeView::branch:has-children:closed {
    image: url("$branch_closed_url");
}

QTreeWidget::branch:has-children:open, QTreeView::branch:has-children:open {
    image: url("$branch_open_url");
}

/* ============ Редактор кода ============
   Подсветка синтаксиса задаётся НЕ здесь, а программно в ui/lexers.py
   теми же цветами палитры (syn_*): QSS не управляет стилями лексера
   Scintilla. Здесь — только рамка/фон виджета и шрифт в pt.
     [section]         -> syn_section   (#C578DD)
     ![section]        -> syn_override  (#FFC66D)
     !![section]       -> syn_deleted   (#F14C4C)
     @[section]        -> syn_safe      (#4EC9B0)
     ключи             -> syn_key       (#9CDCFE)
     значения          -> syn_value     (#B5CEA8)
     списки через ','  -> syn_list_value (#9ACD8B)
     числа             -> syn_number    (#6897BB)
     true/false        -> syn_constant  (#CC7832)
     #include          -> syn_directive (#CC7832)
     комментарии       -> syn_comment   (#6A9955)
     строка-override   -> selection     (#2E436E) */

QTextEdit, QPlainTextEdit {
    background-color: $bg_main;
    color: $text_dock;
    border: none;
    selection-background-color: $selection;
    selection-color: $text_bright;
    font-family: $mono_font_family;
    font-size: ${editor_font_size_pt}pt;
    padding: 4px;
}

QsciScintillaBase, QsciScintilla {
    background-color: $bg_main;
    color: $text;
    border: none;
    selection-background-color: $selection;
    selection-color: $text_bright;
    font-family: $mono_font_family;
    font-size: ${editor_font_size_pt}pt;
}

/* ============ Вкладки редактора ============ */

QTabWidget::pane {
    border: none;
    border-top: 1px solid $border;
    background-color: $bg_main;
}

QTabBar {
    background-color: $tab_bg;
    qproperty-drawBase: false;
}

QTabBar::tab {
    background-color: $tab_bg;
    color: $tab_text;
    border: none;
    border-right: 1px solid $border;
    padding: 7px 16px;
    min-width: 80px;
}

QTabBar::tab:hover {
    background-color: $hover;
    color: $text;
}

QTabBar::tab:selected {
    background-color: $tab_bg_selected;
    color: $tab_text_selected;
    border-bottom: 2px solid $accent;
}

QTabBar::tab:!selected {
    margin-top: 0px;
}

/* ============ Кнопка закрытия вкладки ============ */
QTabBar::close-button {
    subcontrol-position: right;
    image: url("$tab_close_url");
    background: transparent;
    border-radius: 3px;
    width: 16px;
    height: 16px;
    margin: 2px;
}

QTabBar::close-button:hover {
    image: url("$tab_close_hover_url");
    background-color: $border_hover;
}

QTabBar::close-button:pressed {
    image: url("$tab_close_pressed_url");
    background-color: $accent_red;
    border-radius: 3px;
}

/* ============ Нижняя консоль (единая панель во всю ширину) ============ */

QTabWidget#bottomPanel QTabBar,
QTabWidget#bottomPanel QTabBar::tab {
    background-color: $bg_panel;
}

QTabWidget#bottomPanel QTabBar::tab:selected {
    background-color: $bg_main;
    border-bottom: 2px solid $accent_teal;
}

/* Цвета уровней лога задаются в коде полями палитры:
   INFO  -> accent_teal
   WARN  -> accent_amber
   ERROR -> accent_red */

/* ============ Строка состояния ============ */

QStatusBar {
    background-color: $bg_panel;
    color: $text_dock;
    border-top: 1px solid $border;
    padding: 2px 8px;
    font-size: ${ui_font_size_small_pt}pt;
}

QStatusBar::item {
    border: none;
}

QStatusBar QLabel {
    color: $text_dock;
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
    background: $scroll_handle;
    min-height: 30px;
    border-radius: 5px;
    margin: 2px;
}

QScrollBar::handle:vertical:hover {
    background: $scroll_handle_hover;
}

QScrollBar::handle:vertical:pressed {
    background: $accent;
}

QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 0px;
    border: none;
}

QScrollBar::handle:horizontal {
    background: $scroll_handle;
    min-width: 30px;
    border-radius: 5px;
    margin: 2px;
}

QScrollBar::handle:horizontal:hover {
    background: $scroll_handle_hover;
}

QScrollBar::handle:horizontal:pressed {
    background: $accent;
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
    background-color: $border;
}

QSplitter::handle:horizontal {
    width: 1px;
}

QSplitter::handle:vertical {
    height: 1px;
}

QSplitter::handle:hover {
    background-color: $accent;
}

/* ============ Группы и метки ============ */

QGroupBox {
    background-color: $bg_panel;
    border: 1px solid $border;
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
    color: $text_dock;
}

QLabel {
    background: transparent;
    color: $text;
    border: none;
}

QLabel[role="muted"] {
    color: $text_muted;
}

QLabel[role="accent"] {
    color: $accent_teal;
}

/* ============ Поля ввода ============ */

QLineEdit {
    background-color: $bg_main;
    color: $text;
    border: 1px solid $border;
    border-radius: 4px;
    padding: 4px 8px;
    selection-background-color: $selection;
    selection-color: $text_bright;
}

QLineEdit:hover {
    border-color: $border_hover;
}

QLineEdit:focus {
    border-color: $accent;
}

QLineEdit:disabled {
    background-color: $bg_panel;
    color: $text_disabled;
}

QLineEdit[role="search"] {
    background-color: $bg_titlebar;
    border-radius: 10px;
    padding: 4px 12px;
}

/* ============ Выпадающие списки ============ */

QComboBox {
    background-color: $bg_titlebar;
    color: $text;
    border: 1px solid $border_hover;
    border-radius: 4px;
    padding: 4px 8px;
    min-height: 18px;
}

QComboBox:hover {
    border-color: $border_hover;
}

QComboBox:focus {
    border-color: $accent;
}

QComboBox::drop-down {
    border: none;
    width: 20px;
}

QComboBox QAbstractItemView {
    background-color: $bg_panel;
    color: $text;
    border: 1px solid $border_hover;
    border-radius: 4px;
    selection-background-color: $selection;
    selection-color: $text_bright;
    padding: 2px;
}

/* ============ Чекбоксы и радиокнопки ============ */

QCheckBox {
    background: transparent;
    color: $text;
    spacing: 8px;
}

QCheckBox::indicator {
    width: 15px;
    height: 15px;
    border: 1px solid $border_hover;
    border-radius: 4px;
    background-color: $bg_main;
}

QCheckBox::indicator:hover {
    border-color: $accent;
}

QCheckBox::indicator:checked {
    background-color: $accent;
    border-color: $accent;
}

QCheckBox::indicator:disabled {
    background-color: $bg_panel;
    border-color: $border;
}

QRadioButton {
    background: transparent;
    color: $text;
    spacing: 8px;
}

QRadioButton::indicator {
    width: 15px;
    height: 15px;
    border: 1px solid $border_hover;
    border-radius: 8px;
    background-color: $bg_main;
}

QRadioButton::indicator:hover {
    border-color: $accent;
}

QRadioButton::indicator:checked {
    background-color: $accent;
    border-color: $accent;
}

/* ============ Таблицы (Patch Manager, Value Sources) ============ */

QTableView, QTableWidget {
    background-color: $bg_main;
    alternate-background-color: $bg_table_alt;
    color: $text;
    border: none;
    gridline-color: $border;
    selection-background-color: $selection;
    selection-color: $text_bright;
}

QTableView::item {
    padding: 4px 8px;
    border: none;
}

QTableView::item:selected {
    background-color: $selection;
    color: $text_bright;
}

QHeaderView {
    background-color: $bg_panel;
    border: none;
    border-bottom: 1px solid $border;
}

QHeaderView::section {
    background-color: $bg_panel;
    color: $text_dock;
    border: none;
    border-right: 1px solid $border;
    padding: 6px 8px;
    font-weight: 600;
}

QHeaderView::section:hover {
    background-color: $hover;
    color: $text_bright;
}

/* ============ Списки (Modules / plugin browser) ============ */

QListView, QListWidget {
    background-color: $bg_panel;
    color: $text;
    border: none;
    padding: 2px;
}

QListView::item {
    padding: 6px 8px;
    border-radius: 4px;
}

QListView::item:hover {
    background-color: $hover;
}

QListView::item:selected {
    background-color: $selection;
    color: $text_bright;
}

/* ============ Подсказки ============ */

QToolTip {
    background-color: $bg_panel;
    color: $text;
    border: 1px solid $border_hover;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: ${ui_font_size_small_pt}pt;
}

/* ============ Прогресс и ползунки ============ */

QProgressBar {
    background-color: $bg_panel;
    border: none;
    border-radius: 4px;
    height: 8px;
    text-align: center;
    color: transparent;
}

QProgressBar::chunk {
    background-color: $accent;
    border-radius: 4px;
}

QSlider::groove:horizontal {
    background: $border;
    height: 4px;
    border-radius: 2px;
}

QSlider::handle:horizontal {
    background: $text;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}

QSlider::handle:horizontal:hover {
    background: $accent;
}

QSlider::sub-page:horizontal {
    background: $accent;
    border-radius: 2px;
}

/* ============ Диалоги ============ */

QDialog {
    background-color: $bg_main;
}

QMessageBox {
    background-color: $bg_main;
}

/* ============ Разделители окна ============ */

QMainWindow::separator {
    background-color: $border;
    width: 1px;
    height: 1px;
}

QMainWindow::separator:hover {
    background-color: $accent;
}
""")


# ======================================================================
# Сборка QSS и применение темы
# ======================================================================

def build_stylesheet(palette: Palette = PALETTE) -> str:
    """Собирает QSS из палитры: цвета, шрифты и url иконок."""
    closed, opened = ensure_branch_icons(palette)
    tab_close, tab_close_hover, tab_close_pressed = ensure_tab_close_icons(palette)
    
    mapping = asdict(palette)
    mapping["branch_closed_url"] = closed
    mapping["branch_open_url"] = opened
    
    # Добавляем иконки крестика
    mapping["tab_close_url"] = tab_close
    mapping["tab_close_hover_url"] = tab_close_hover
    mapping["tab_close_pressed_url"] = tab_close_pressed
    
    return _QSS.substitute(mapping)


_DARK_THEME_CACHE: Optional[str] = None


def _get_dark_theme() -> str:
    """Лениво собирает и кэширует DARK_THEME.

    Ленивость важна: ensure_branch_icons() рисует QImage/QPainter, что
    безопасно делать после создания QApplication (т.е. в apply_theme),
    а не на импорте модуля.
    """
    global _DARK_THEME_CACHE
    if _DARK_THEME_CACHE is None:
        _DARK_THEME_CACHE = build_stylesheet(PALETTE)
    return _DARK_THEME_CACHE


def __getattr__(name):  # PEP 562: модульный атрибут DARK_THEME по требованию
    if name == "DARK_THEME":
        return _get_dark_theme()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def apply_theme(app, palette: Optional[Palette] = None) -> Palette:
    """Применяет тёмную тему V2.1 ко всему приложению PyQt5.

    Возвращает применённую палитру. Для кастомной палитры QSS собирается
    заново (иконки ветвей перерисовываются её цветом текста).
    """
    if palette is None:
        app.setStyleSheet(_get_dark_theme())
        return PALETTE
    app.setStyleSheet(build_stylesheet(palette))
    return palette


# Розовые цвета опасного режима Override.
OVERRIDE_PINK = "#C05A7A"
OVERRIDE_PINK_SELECTED = "#D66B8B"


def apply_override_theme(app, enabled: bool) -> Palette:
    """Включает/выключает розовые вкладки редактора в режиме Override.

    Рамки и разделители не меняются. Возвращает применённую палитру.
    """
    if not enabled:
        app.setStyleSheet(_get_dark_theme())
        return PALETTE

    palette = replace(
        PALETTE,
        tab_bg=OVERRIDE_PINK,
        tab_bg_selected=OVERRIDE_PINK_SELECTED,
        tab_text="#FFE3EC",
        tab_text_selected="#FFFFFF",
    )
    app.setStyleSheet(build_stylesheet(palette))
    return palette


def apply_window_caption(window, enabled: bool) -> None:
    """Красит нативную шапку окна Windows в розовый/обычный цвет."""
    if os.name != "nt" or window is None:
        return
    try:
        import ctypes

        hwnd = int(window.winId())
        dwm = ctypes.windll.dwmapi
        # DWMWA_CAPTION_COLOR = 35, DWMWA_TEXT_COLOR = 36,
        # DWMWA_BORDER_COLOR = 34. COLORREF = 0x00BBGGRR.
        if enabled:
            caption = _colorref(OVERRIDE_PINK)
            text = _colorref("#FFFFFF")
            border = _colorref(OVERRIDE_PINK)
            for attr, value in (
                (35, caption),
                (36, text),
                (34, border),
            ):
                dwm.DwmSetWindowAttribute(
                    ctypes.c_void_p(hwnd),
                    attr,
                    ctypes.byref(ctypes.c_int(value)),
                    ctypes.sizeof(ctypes.c_int),
                )
        else:
            default = ctypes.c_int(0xFFFFFFFF)
            for attr in (35, 36, 34):
                dwm.DwmSetWindowAttribute(
                    ctypes.c_void_p(hwnd),
                    attr,
                    ctypes.byref(default),
                    ctypes.sizeof(ctypes.c_int),
                )
    except Exception:
        pass


def _colorref(color: str) -> int:
    value = color.lstrip("#")
    r = int(value[0:2], 16)
    g = int(value[2:4], 16)
    b = int(value[4:6], 16)
    return (b << 16) | (g << 8) | r