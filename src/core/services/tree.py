# -*- coding: utf-8 -*-
"""Дерево, пути, файлы и граф-цели ядра.

Узкий сервис для композиционного корня: не знает о патч-конвейере,
каталогах данных и предпросмотре.
"""

from __future__ import annotations

from pathlib import Path

from core.patching.targets import resolve_patch_target
from core.tools.fs import (
    find_system_ltx,
    game_root_from_scan_dir,
    read_text_safe,
)
from core.tools.text_search import search_text_files
from core.tree.builder import add_virtual_file, remove_virtual_file
from core.tree.query import search_nodes, sort_nodes


class TreeServices:
    """Операции над деревом VFS и путями.

    ``cache_control`` нужен только для ``build_tree(clean=True)`` и
    инвалидации перед пересборкой. Он передаётся composition root'ом.
    """

    def __init__(
        self,
        tree_builder,
        tree_query,
        snapshot_service=None,
        cache_service=None,
        cache_control=None,
    ):
        self.tree_builder = tree_builder
        self.tree_query = tree_query
        self.snapshot_service = snapshot_service
        self.cache_service = cache_service
        self.cache_control = cache_control

    def find_system_ltx(self, sel):
        """Найти system.ltx в указанной директории."""
        return find_system_ltx(sel)

    def resolve_scan_dir(self, config_dir):
        """Определить каталог сканирования VFS по выбранному пути."""
        config_dir = Path(config_dir)
        if (config_dir / "system.ltx").is_file():
            return config_dir
        system = self.find_system_ltx(config_dir)
        return Path(system).parent if system is not None else None

    def build_tree(
        self,
        scan_dir,
        system_root,
        log=None,
        clean=False,
        overlay=None,
    ):
        """Построить дерево файлов с наложением черновиков проекта."""
        if clean and self.cache_control is not None:
            self.cache_control.invalidate_cache()

        overlay_revision = (
            getattr(overlay, "revision", "") if overlay else ""
        )
        snapshot_service = self.snapshot_service
        snapshot_id = ""
        use_cache = True
        if snapshot_service is not None and not clean:
            snapshot = snapshot_service.ensure(
                scan_dir,
                system_root,
                overlay_revision=overlay_revision,
                refresh=False,
            )
            snapshot_id = snapshot.id
        elif clean and snapshot_service is not None:
            # Пересборка VFS уже сканирует файлы: снимок строится по
            # готовому лесу, без второго обхода каталога.
            use_cache = False

        forest = self.tree_builder.build(
            scan_dir=scan_dir,
            system_root=system_root,
            log=log,
            use_cache=use_cache,
            overlay=overlay,
            snapshot_id=snapshot_id,
            overlay_revision=overlay_revision,
        )

        if clean and snapshot_service is not None:
            snapshot = snapshot_service.observe_forest(
                forest,
                overlay_revision=overlay_revision,
            )
            forest.cache_id = snapshot.id
            forest.overlay_revision = snapshot.overlay_revision
            if self.cache_service is not None:
                self.cache_service.set(
                    "tree",
                    self.tree_builder.cache_key(
                        scan_dir,
                        system_root,
                        overlay,
                        snapshot.id,
                    ),
                    forest,
                    deps=(scan_dir, system_root),
                    snapshot_id=snapshot.id,
                )
        return forest

    def sort_nodes(self, forest, nodes, col="path", reverse=False):
        """Отсортировать узлы дерева."""
        return sort_nodes(forest, nodes, col=col, reverse=reverse)

    def search_nodes(self, forest, text):
        """Поиск по дереву файлов."""
        return search_nodes(forest, text)

    def search_text(
        self,
        forest,
        query,
        source_files=None,
        case_sensitive=False,
        log=None,
        cancel_check=None,
        progress=None,
    ):
        """Полнотекстовый поиск по собранному VFS с учётом overlay."""
        return search_text_files(
            forest.scan_dir,
            query,
            source_files=source_files,
            case_sensitive=case_sensitive,
            log=log,
            cancel_check=cancel_check,
            progress=progress,
        )

    def rel_path(self, forest, path):
        """Получить относительный путь файла."""
        return forest.rel_paths.get(path, str(path))

    def relative_path(self, forest, path):
        """Каноническое имя операции для SDK."""
        return self.rel_path(forest, path)

    def forest_game_root(self, forest):
        """Корень gamedata VFS: родитель каталога ``configs``."""
        return game_root_from_scan_dir(getattr(forest, "scan_dir", None))

    def game_root(self, forest):
        """Каноническое имя операции для SDK."""
        return self.forest_game_root(forest)

    def patch_target_info(self, forest, path):
        """Полная информация о цели патча (корни/неоднозначность)."""
        return resolve_patch_target(forest, path)

    def patch_target(self, forest, path):
        """Единственная цель патча или ``None`` при неоднозначности."""
        return self.patch_target_info(forest, path).root_path

    def add_virtual_file(self, forest, path, text):
        """Добавить новый черновик в существующее дерево VFS."""
        return add_virtual_file(forest, path, text)

    def remove_virtual_file(self, forest, path):
        """Убрать виртуальный файл из дерева VFS."""
        return remove_virtual_file(forest, path)

    def read_text(self, path):
        """Прочитать текстовый файл с автоопределением кодировки."""
        return read_text_safe(path)

    def read_xml_text(self, path):
        """Прочитать XML строго как windows-1251 (правило X-Ray)."""
        from core.tools.fs import read_xml_text

        return read_xml_text(path)


__all__ = ["TreeServices"]
