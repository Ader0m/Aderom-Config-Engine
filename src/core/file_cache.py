# -*- coding: utf-8 -*-
"""Файловый кэш и кэш промежуточных данных."""


class FileCacheSystem:
    """
    Центральный кэш тяжёлых данных.

    Сейчас это минимальный каркас для соответствия архитектуре.
    В него можно класть:
    - дерево;
    - результаты запросов дерева;
    - результаты резолвера;
    - граф связей;
    - превью патчей.
    """

    def __init__(self):
        self._data = {}
        self._snapshot_id = None

    def set_snapshot(self, snapshot_id):
        """
        Если снимок файловой системы изменился,
        кэш считается недействительным.
        """
        if snapshot_id != self._snapshot_id:
            self.clear()
            self._snapshot_id = snapshot_id

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value

    def invalidate(self, prefix=None):
        """
        Инвалидация ключей.

        Если prefix равен None, очищается весь кэш.
        Если prefix передан, удаляются ключи, начинающиеся с него.
        """
        if prefix is None:
            self.clear()
            return

        for k in list(self._data.keys()):
            if isinstance(k, tuple) and k[:len(prefix)] == prefix:
                del self._data[k]

    def clear(self):
        self._data.clear()