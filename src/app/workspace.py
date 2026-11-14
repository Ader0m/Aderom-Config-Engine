# -*- coding: utf-8 -*-
"""Единственный владелец текущего VFS-леса и виртуальных файлов проекта.

Платформа и проектный слой не должны independently добавлять/удалять
файлы в ``LtxForest``. Все такие изменения проходят здесь, чтобы после
них можно было один раз опубликовать новое поколение дерева.
"""

from __future__ import annotations

from pathlib import Path


class WorkspaceService:
    def __init__(self, services=None):
        self._services = services
        self._forest = None
        self._config_dir = None
        self._generation = 0
        self._virtual_paths = set()
        self.last_added_paths = set()
        self.last_removed_paths = set()
        self.last_removed_roots = set()

    # -------------------------------------------------- состояние
    @property
    def forest(self):
        return self._forest

    @property
    def config_dir(self):
        return self._config_dir

    @property
    def generation(self):
        return self._generation

    @property
    def virtual_paths(self):
        return self._virtual_paths

    def set_forest(self, forest, config_dir=None):
        self._forest = forest
        if config_dir is not None:
            self._config_dir = Path(config_dir)
        self._generation += 1
        return forest

    def root_for(self, path):
        """Корень загрузки, к которому относится файл."""
        forest = self._forest
        if forest is None:
            return None
        current = Path(path)
        seen = set()
        while True:
            key = str(current)
            if key in seen:
                return current
            seen.add(key)
            parents = getattr(forest, "node_parent", {}) or {}
            parent = parents.get(current)
            if parent is None:
                parent = parents.get(str(current))
            if parent is None:
                return current
            current = Path(parent)

    def roots_for(self, paths):
        """Уникальные корни, в чьи поддеревья входят заданные файлы.

        Один файл может входить сразу в несколько корней (общий include,
        DLTX-привязка к нескольким mod-файлам), поэтому обход идёт по
        всем родителям, а не только по одному ``node_parent``. Иначе
        правка общего файла инвалидировала бы лишь часть кеша.

        Путь, который не удаётся связать с корнем дерева, не попадает в
        результат: вызывающая сторона обязана в этом случае откатиться к
        полной инвалидации.
        """
        forest = self._forest
        if forest is None:
            return set()

        parents_map = getattr(forest, "include_parents", {}) or {}
        node_parent = getattr(forest, "node_parent", {}) or {}
        forest_roots = {
            Path(root) for root in (getattr(forest, "roots", ()) or ())
        }

        roots = set()
        for path in paths or ():
            stack = [Path(path)]
            seen = set()
            while stack:
                node = stack.pop()
                key = str(node)
                if key in seen:
                    continue
                seen.add(key)

                parents = parents_map.get(node)
                if not parents:
                    parents = parents_map.get(key)
                parents = [Path(parent) for parent in (parents or ())]

                if not parents:
                    parent = node_parent.get(node)
                    if parent is None:
                        parent = node_parent.get(key)
                    if parent is not None:
                        parents = [Path(parent)]

                if parents:
                    stack.extend(parents)
                elif node in forest_roots:
                    roots.add(node)

        return roots

    def clear(self):
        self._forest = None
        self._virtual_paths.clear()
        self._generation += 1

    # -------------------------------------------------- VFS проекта
    def apply_overlay(self, overlay, services=None):
        """Привести виртуальные файлы леса в соответствие с overlay.

        Возвращает ``(added, removed)``. Оригинальные файлы на диске не
        изменяются: только ``forest.files``/``forest.xml_files``.
        """
        forest = self._forest
        services = services if services is not None else self._services
        if forest is None or services is None:
            return 0, 0

        source_files = getattr(overlay, "source_files", None) or {}
        wanted = {
            Path(path)
            for path in source_files
            if Path(path).suffix.lower() in (".ltx", ".xml")
        }
        self.last_added_paths = set()
        self.last_removed_paths = set(self._virtual_paths - wanted)
        self.last_removed_roots = self.roots_for(self.last_removed_paths)

        removed = 0
        for path in list(self._virtual_paths - wanted):
            try:
                if services.remove_virtual_file(forest, path):
                    removed += 1
            finally:
                self._virtual_paths.discard(path)

        added = 0
        for path in sorted(wanted, key=lambda item: str(item).lower()):
            if path in forest.files or path in forest.xml_files:
                continue
            text = str(source_files.get(path, ""))
            try:
                if services.add_virtual_file(forest, path, text):
                    self._virtual_paths.add(path)
                    self.last_added_paths.add(path)
                    added += 1
            except Exception:
                continue

        if added or removed:
            self._generation += 1
        return added, removed
