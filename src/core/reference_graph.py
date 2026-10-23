# -*- coding: utf-8 -*-
"""ReferenceGraphBuilder — построение графа связей по имени секции."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


# ------------------------------------------------------------ типы

class EdgeType(Enum):
    DECLARATION = "declaration"
    INHERITANCE = "inheritance"
    LTX_REFERENCE = "ltx_reference"
    SCRIPT_REFERENCE = "script_reference"
    XML_REFERENCE = "xml_reference"


@dataclass
class MatchInfo:
    edge_type: EdgeType
    line_number: int
    line_text: str


@dataclass
class GraphNode:
    display_name: str
    file_path: Optional[Path]
    first_match_line: int
    matches: list[MatchInfo] = field(default_factory=list)


@dataclass
class SectionGraph:
    section_name: str
    root: GraphNode
    children: list[GraphNode] = field(default_factory=list)


# ------------------------------------------------------------ заглушка кэша

class DisabledCache:
    """Кэш отключён. Всегда возвращает None."""

    def get(self, key):
        return None

    def set(self, key, value):
        pass

    def clear(self):
        pass


# ------------------------------------------------------------ сервис

class ReferenceGraphBuilder:
    """
    Строит граф связей по имени секции.

    Работает только с готовым деревом (LtxForest) от TreeBuilder.
    Для .ltx файлов использует пути из леса.
    Для .script файлов сканирует scan_dir леса.
    Для .xml — заглушка.
    """

    def __init__(self, cache=None):
        self.cache = cache or DisabledCache()

    def build_for_section(self, forest, section_name: str) -> SectionGraph:
        """
        Построить граф связей для секции.

        Параметры:
            forest: LtxForest от TreeBuilder
            section_name: имя секции (например, "raid_material_1")

        Возвращает:
            SectionGraph с корневым узлом (объявление) и дочерними узлами (файлы с упоминаниями)
        """
        section = section_name.lower().strip()

        # 1. Ищем объявление секции
        root_node = self._find_declaration(forest, section)
        if root_node is None:
            raise ValueError(f"Секция '{section}' не найдена в дереве")

        children = []

        # 2. Сканируем .ltx файлы из леса (кроме файла объявления)
        for file_path in forest.files:
            if file_path == root_node.file_path:
                continue

            matches = self._scan_ltx_file(file_path, section)
            if matches:
                children.append(GraphNode(
                    display_name=file_path.name,
                    file_path=file_path,
                    first_match_line=matches[0].line_number,
                    matches=matches,
                ))

        # 3. Сканируем .script файлы в scan_dir
        scan_dir = Path(forest.scan_dir)
        if scan_dir.is_dir():
            for script_path in scan_dir.rglob("*.script"):
                matches = self._scan_script_file(script_path, section)
                if matches:
                    children.append(GraphNode(
                        display_name=script_path.name,
                        file_path=script_path,
                        first_match_line=matches[0].line_number,
                        matches=matches,
                    ))

        # 4. XML — заглушка (пока не реализуем)

        return SectionGraph(
            section_name=section,
            root=root_node,
            children=children,
        )

    # ------------------------------------------------------------ поиск объявления

    def _find_declaration(self, forest, section: str) -> Optional[GraphNode]:
        """Ищем объявление секции [секция] или [секция]:... в .ltx файлах."""
        for file_path in forest.files:
            text = self._read_file(file_path)
            if not text:
                continue

            for line_num, line in enumerate(text.splitlines(), 1):
                if self._is_declaration(line, section):
                    return GraphNode(
                        display_name=section,
                        file_path=file_path,
                        first_match_line=line_num,
                        matches=[MatchInfo(
                            edge_type=EdgeType.DECLARATION,
                            line_number=line_num,
                            line_text=line,
                        )],
                    )
        return None

    def _is_declaration(self, line: str, section: str) -> bool:
        """Проверяет, является ли строка объявлением секции."""
        stripped = line.strip()
        # [секция] или [секция]:...
        if stripped.startswith(f"[{section}]"):
            return True
        if stripped.startswith(f"[{section}]:"):
            return True
        return False

    # ------------------------------------------------------------ сканирование .ltx

    def _scan_ltx_file(self, file_path: Path, section: str) -> list[MatchInfo]:
        """Сканирует .ltx файл на совпадения."""
        text = self._read_file(file_path)
        if not text:
            return []

        matches = []
        for line_num, line in enumerate(text.splitlines(), 1):
            # Быстрая проверка: есть ли секция в строке?
            if section not in line.lower():
                continue

            edge_type = self._classify_ltx_line(line, section)
            matches.append(MatchInfo(
                edge_type=edge_type,
                line_number=line_num,
                line_text=line,
            ))

        return matches

    def _classify_ltx_line(self, line: str, section: str) -> EdgeType:
        """Классифицирует строку .ltx файла."""
        stripped = line.strip()

        # 1. Объявление: [секция] или [секция]:...
        if self._is_declaration(line, section):
            return EdgeType.DECLARATION

        # 2. Наследование: [ребенок]:...,секция,...
        if self._is_inheritance(stripped, section):
            return EdgeType.INHERITANCE

        # 3. Всё остальное — LTX_REFERENCE
        return EdgeType.LTX_REFERENCE

    def _is_inheritance(self, line: str, section: str) -> bool:
        """
        Проверяет наследование: [ребенок]:родитель1,секция,родитель2
        Секция должна быть в списке родителей после ]:
        """
        if not line.startswith("["):
            return False

        bracket_end = line.find("]:")
        if bracket_end == -1:
            return False

        parents_part = line[bracket_end + 2:]
        parents = [p.strip().lower() for p in parents_part.split(",")]
        return section in parents

    # ------------------------------------------------------------ сканирование .script

    def _scan_script_file(self, file_path: Path, section: str) -> list[MatchInfo]:
        """Сканирует .script файл на совпадения."""
        text = self._read_file(file_path)
        if not text:
            return []

        matches = []
        for line_num, line in enumerate(text.splitlines(), 1):
            if section in line.lower():
                matches.append(MatchInfo(
                    edge_type=EdgeType.SCRIPT_REFERENCE,
                    line_number=line_num,
                    line_text=line,
                ))

        return matches

    # ------------------------------------------------------------ чтение файлов

    def _read_file(self, file_path: Path) -> str:
        """Читает файл. Кэш отключён — всегда читаем напрямую."""
        cached = self.cache.get(("file", str(file_path)))
        if cached is not None:
            return cached

        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            text = ""

        # Не кэшируем (заглушка)
        return text