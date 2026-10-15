# -*- coding: utf-8 -*-
"""Тема оформления."""

DARK_THEME = """
QMainWindow {
    background-color: #1E1E1E;
}

QMenuBar {
    background-color: #2D2D30;
    color: #D4D4D4;
}

QMenuBar::item:selected {
    background-color: #3E3E42;
}

QMenu {
    background-color: #2D2D30;
    color: #D4D4D4;
}

QMenu::item:selected {
    background-color: #094771;
}

QTreeWidget {
    background-color: #252526;
    color: #D4D4D4;
    border: none;
}

QTreeWidget::item:selected {
    background-color: #094771;
}

QDockWidget {
    color: #D4D4D4;
}

QDockWidget::title {
    background-color: #2D2D30;
    padding: 4px;
}

QTextEdit {
    background-color: #1E1E1E;
    color: #D4D4D4;
    border: none;
}

QStatusBar {
    background-color: #007ACC;
    color: #FFFFFF;
}
"""


def apply_theme(app):
    app.setStyleSheet(DARK_THEME)