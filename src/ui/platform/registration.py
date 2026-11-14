# -*- coding: utf-8 -*-
"""Реестры возможностей, которые регистрируют модули.

Платформа не обязана знать, какой модуль зарегистрировал меню или
файловое действие. Здесь хранится только описание и порядок регистрации.
"""

from __future__ import annotations


class RegistrationHub:
    def __init__(self):
        self._menu = []      # (menu_title, label, callback, shortcut, action)
        self._tree = []      # (label, handler(path), predicate(path)|None)
        self._file = []      # (label, handler(path), predicate(path)|None)

    # -------------------------------------------------- меню
    def register_menu(self, menu_title, label, callback, shortcut=None,
                      action=None):
        self._menu.append(
            (menu_title, label, callback, shortcut, action)
        )

    @property
    def menu_registrations(self):
        return list(self._menu)

    # -------------------------------------------------- действия дерева
    def register_tree_context_action(self, label, handler, predicate=None):
        self._tree.append((label, handler, predicate))

    @property
    def context_actions(self):
        return list(self._tree)

    # -------------------------------------------------- действия вкладок
    def register_file_action(self, label, handler, predicate=None):
        self._file.append((label, handler, predicate))

    @property
    def file_actions(self):
        return list(self._file)
