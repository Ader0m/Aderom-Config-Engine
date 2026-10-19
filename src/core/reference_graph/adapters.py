"""Адаптеры портов GraphInput для текущих сервисов ядра.

Подключают reference_graph к:
- TreeBuilder / LtxForest -> TreeStructurePort
- LtxResolverAdapter / LtxResolver -> ResolvedTreePort
- FileCacheSystem -> ParsedRawPort (заглушки для xml/скриптов/комментариев)

Важное ограничение v1: LtxResolver после load() очищает данные о наследовании
(base_parent_data_map, override_parent_data_map), поэтому в v1 родители секций
берутся из сырого парсинга, а не из финального слепка. Это временное решение;
в v2 можно расширить Resolver4.py, чтобы он сохранял эту информацию.
"""
import os

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import FrozenSet, Iterable, Mapping, Optional, Tuple

from .graph_input import (
    Line,
    ParsedRawPort,
    RawComment,
    ResolvedSection,
    ResolvedTreePort,
    ScriptFile,
    TreeFile,
    TreeSection,
    TreeStructurePort,
    XmlFile,
)
from .models import ChainLink


# --------------------------------------------------------------------------- #
# TreeStructurePort адаптер
# --------------------------------------------------------------------------- #

class ForestStructureAdapter(TreeStructurePort):
    """Адаптер LtxForest -> TreeStructurePort.

    Берёт только структурные данные: списки файлов, их относительные пути.
    Не разрешает значения секций.
    """

    def __init__(self, forest, vfs_snapshot_id: Optional[str] = None):
        self._forest = forest
        # Если snapshot_id не передан, вычисляем из файлов
        if vfs_snapshot_id is None:
            files_str = "\n".join(str(f) for f in sorted(forest.files))
            self._vfs_snapshot = hashlib.md5(files_str.encode()).hexdigest()
        else:
            self._vfs_snapshot = vfs_snapshot_id

    def vfs_snapshot(self) -> str:
        return self._vfs_snapshot

    def ltx_file_names(self) -> Tuple[str, ...]:
        return tuple(
            self._forest.rel_paths.get(f, str(f)) for f in self._forest.files
        )

    def xml_file_names(self) -> Tuple[str, ...]:
        # Заглушка: в v1 не сканируем xml
        return ()

    def script_file_names(self) -> Tuple[str, ...]:
        # Заглушка: в v1 не сканируем скрипты
        return ()


# --------------------------------------------------------------------------- #
# ResolvedTreePort адаптер
# --------------------------------------------------------------------------- #

class ResolverTreeAdapter(ResolvedTreePort):
    """Адаптер LtxResolver -> ResolvedTreePort.

    Создаёт TreeFileView и ResolvedSectionView из данных резолвера.

    Ограничение v1: LtxResolver не сохраняет информацию о наследовании после load(),
    поэтому родители секций берутся как пустые кортежи. В v2 можно расширить
    Resolver4.py, чтобы он сохранял base_parent_data_map, или парсить сырые файлы.
    """

    def __init__(self, resolver, vfs_snapshot_id: str, resolver_version: str = "4.0",
                 forest=None):
        self._resolver = resolver
        self._vfs_snapshot = vfs_snapshot_id
        self._resolver_version = resolver_version
        self._stem_to_rel = {}
        if forest is not None:
            for f in forest.files:
                stem = os.path.splitext(f.name)[0].lower()
                if stem not in self._stem_to_rel:
                    self._stem_to_rel[stem] = forest.rel_paths.get(f, str(f))

    def vfs_snapshot(self) -> str:
        return self._vfs_snapshot

    def resolver_version(self) -> str:
        return self._resolver_version

    def iter_tree_files(self) -> Iterable[TreeFile]:
        """Один проход по данным резолвера: файл -> секции -> строки."""
        files: dict = {}

        for section_name, sect in self._resolver.data:
            for item in sect.data:
                filename = getattr(item, "filename", "") or ""
                if not filename:
                    continue

                value = item.second
                if hasattr(value, "name"):
                    value_str = "<%s>" % value.name
                elif value is None:
                    value_str = ""
                else:
                    value_str = str(value)

                lines = files.setdefault(filename, {}).setdefault(section_name, [])
                lines.append(Line(
                    key=item.first or "",
                    value=value_str,
                    owner=filename,
                    disk_path=None,
                    chain=(ChainLink(
                        file=filename,
                        depth=getattr(item, "depth", 0),
                        value=value_str,
                        operator="",
                        insert_index=getattr(item, "insertion_index", 0),
                    ),),
                ))

        for file_key, sections in files.items():
            yield TreeFile(
                name=file_key,
                sections=tuple(
                    TreeSection(name=name, lines=tuple(lines))
                    for name, lines in sections.items()
                ),
            )

    def iter_resolved_sections(self) -> Iterable[ResolvedSection]:
        """Секции с финальными ключами и родителями."""
        for section_name, sect in self._resolver.data:
            keys = {}
            for item in sect.data:
                if item.first:
                    value = item.second
                    if hasattr(value, "name"):  # DLTXToken
                        keys[item.first] = f"<{value.name}>"
                    elif value is None:
                        keys[item.first] = ""
                    else:
                        keys[item.first] = str(value)

            # В v1 родители не доступны из Resolver4.py после load()
            # Заглушка: пустой кортеж
            parents = ()

            yield ResolvedSection(
                name=section_name,
                parents=parents,
                keys=keys,
            )


# --------------------------------------------------------------------------- #
# ParsedRawPort адаптер (заглушки)
# --------------------------------------------------------------------------- #

class ParsedRawAdapter(ParsedRawPort):
    """Адаптер для сырых данных (комментарии, xml, скрипты).

    В v1 возвращает заглушки: комментарии, xml и скрипты не сканируются.
    В v2 можно добавить парсинг через FileCacheSystem.
    """

    def __init__(self, vfs_snapshot_id: str, parser_version: str = "1.0"):
        self._vfs_snapshot = vfs_snapshot_id
        self._parser_version = parser_version

    def vfs_snapshot(self) -> str:
        return self._vfs_snapshot

    def parser_version(self) -> str:
        return self._parser_version

    def iter_raw_comments(self) -> Iterable[RawComment]:
        # Заглушка: комментарии не извлекаются
        return ()

    def xml_fragments(self, file_name: str) -> Tuple[str, ...]:
        # Заглушка: xml не парсится
        return ()

    def script_lines(self, file_name: str) -> Tuple[str, ...]:
        # Заглушка: скрипты не читаются
        return ()

    def raw_defined_section_names(self) -> FrozenSet[str]:
        # Заглушка: сырые определения не сканируются
        # В v1 полагаемся только на итоговое дерево
        return frozenset()


# --------------------------------------------------------------------------- #
# Фабрика для сборки GraphInput
# --------------------------------------------------------------------------- #

def create_graph_input(
    forest,
    resolver,
    vfs_snapshot_id: Optional[str] = None,
    resolver_version: str = "4.0",
    parser_version: str = "1.0",
):
    """Создаёт GraphInputSnapshot из текущих сервисов ядра.

    Args:
        forest: LtxForest от TreeBuilder
        resolver: LtxResolver (после load()) от LtxResolverAdapter
        vfs_snapshot_id: идентификатор снимка VFS (если None, вычисляется из файлов)
        resolver_version: версия Resolver4.py
        parser_version: версия парсера (для заглушек)

    Returns:
        GraphInputSnapshot, готовый для фазы A reference_graph
    """
    from .graph_input import assemble_graph_input

    # Создаём snapshot_id, если не передан
    if vfs_snapshot_id is None:
        files_str = "\n".join(str(f) for f in sorted(forest.files))
        vfs_snapshot_id = hashlib.md5(files_str.encode()).hexdigest()

    # Создаём адаптеры
    tree_port = ForestStructureAdapter(forest, vfs_snapshot_id)
    resolver_port = ResolverTreeAdapter(resolver, vfs_snapshot_id, resolver_version, forest=forest)
    raw_port = ParsedRawAdapter(vfs_snapshot_id, parser_version)

    # Собираем GraphInputSnapshot
    return assemble_graph_input(tree_port, resolver_port, raw_port)