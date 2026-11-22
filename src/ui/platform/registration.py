# -*- coding: utf-8 -*-
"""Реестры возможностей, которые регистрируют модули.

Каждая регистрация принадлежит модулю и возвращает
:class:`sdk.registrations.RegistrationHandle`. Платформа хранит владельца,
поэтому модуль может снять только свою регистрацию, а загрузчик — все
регистрации модуля одним вызовом scope.
"""

from __future__ import annotations

import threading

from sdk.kernel.registrations import RegistrationHandle, remove_identity


class RegistrationHub:
    def __init__(self):
        # ``(owner, payload)``. Payload сохраняет прежний формат, который
        # читают MainWindow и EditorTabs.
        self._menu = []      # (owner, (menu_title, label, callback, shortcut, action))
        self._tree = []      # (owner, (label, handler(path), predicate(path)|None))
        self._file = []      # (owner, (label, handler(path), predicate(path)|None))
        self._lock = threading.RLock()
        self._listeners = []

    # -------------------------------------------------- служебное
    def _add(self, bucket, owner, payload):
        entry = (str(owner or ""), payload)
        with self._lock:
            bucket.append(entry)
        if bucket is self._menu:
            self._notify()
        return RegistrationHandle(
            lambda: self._remove(bucket, entry),
            entry[0],
        )

    def _remove(self, bucket, entry):
        with self._lock:
            removed = remove_identity(bucket, entry)
        if removed and bucket is self._menu:
            self._notify()
        return removed

    def _notify(self):
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener()
            except Exception:
                # Слушатель — только UI-обновление; ошибка не должна
                # ломать регистрацию/снятие.
                pass

    def add_listener(self, listener):
        """Подписаться на изменение меню (для пересборки menubar)."""
        with self._lock:
            self._listeners.append(listener)
        return RegistrationHandle(
            lambda: self._remove_listener(listener),
        )

    def _remove_listener(self, listener):
        with self._lock:
            return remove_identity(self._listeners, listener)

    # -------------------------------------------------- меню
    def register_menu(self, menu_title, label, callback, shortcut=None,
                      action=None, owner=None):
        return self._add(
            self._menu,
            owner,
            (menu_title, label, callback, shortcut, action),
        )

    @property
    def menu_registrations(self):
        with self._lock:
            return [value for _owner, value in self._menu]

    # -------------------------------------------------- действия дерева
    def register_tree_context_action(self, label, handler, predicate=None,
                                     owner=None):
        return self._add(
            self._tree,
            owner,
            (label, handler, predicate),
        )

    @property
    def context_actions(self):
        with self._lock:
            return [value for _owner, value in self._tree]

    # -------------------------------------------------- действия вкладок
    def register_file_action(self, label, handler, predicate=None, owner=None):
        return self._add(
            self._file,
            owner,
            (label, handler, predicate),
        )

    @property
    def file_actions(self):
        with self._lock:
            return [value for _owner, value in self._file]
