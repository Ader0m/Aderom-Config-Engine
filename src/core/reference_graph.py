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
    TEXTURE_REFERENCE = "texture_reference"


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
    raw_ref: Optional[str] = None

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
        section = section_name.lower().strip()

        # 1. Ищем объявление секции + icons_texture в теле секции
        root_node, texture = self._find_declaration(forest, section)
        if root_node is None:
            raise ValueError(f"Секция '{section}' не найдена в дереве")

        children = []

        # 1.1. Узел текстуры: путь из icons_texture = *
        if texture is not None:
            raw_ref, tex_match = texture
            children.append(GraphNode(
                display_name=self._texture_display_name(raw_ref),
                file_path=root_node.file_path,   # открывать файл объявления на строке icons_texture
                first_match_line=tex_match.line_number,
                matches=[tex_match],
                raw_ref=raw_ref,
            ))

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

        # 4. XML — заглушка

        return SectionGraph(
            section_name=section,
            root=root_node,
            children=children,
        )

    # ------------------------------------------------------------ поиск объявления

    def _find_declaration(self, forest, section: str):
        """
        Ищем объявление секции [секция] или [секция]:... в .ltx файлах.
        Возвращает (root_node, texture), где texture = (raw_path, MatchInfo) или (None, None).
        """
        for file_path in forest.files:
            text = self._read_file(file_path)
            if not text:
                continue

            lines = text.splitlines()
            for idx, line in enumerate(lines):
                if self._is_declaration(line, section):
                    line_num = idx + 1
                    root = GraphNode(
                        display_name=section,
                        file_path=file_path,
                        first_match_line=line_num,
                        matches=[MatchInfo(
                            edge_type=EdgeType.DECLARATION,
                            
                            line_number=line_num,
                            line_text=line,
                        )],
                    )
                    raw_ref, tex_match = self._find_icons_texture(lines, idx + 1)
                    return root, (raw_ref, tex_match) if tex_match else (None, None)

        return None, (None, None)

    def _find_icons_texture(self, lines, start_idx):
        """
        Читает тело секции от строки объявления до следующего заголовка [...]
        и ищет ключ icons_texture = *.
        Возвращает (raw_path, MatchInfo) или (None, None).
        """
        for i in range(start_idx, len(lines)):
            s = lines[i].strip()

            if not s or s.startswith(";"):
                continue

            # начало следующей секции — тело закончилось
            if s.startswith("[") or s.startswith("![") or s.startswith("@[") or s.startswith("!!["):
                break

            low = s.lower()
            if low.startswith("icons_texture") and "=" in s:
                raw = s.split("=", 1)[1].strip()
                if raw:
                    return raw, MatchInfo(
                        edge_type=EdgeType.TEXTURE_REFERENCE,
                        line_number=i + 1,
                        line_text=lines[i],
                    )

        return None, None

    @staticmethod
    def _texture_display_name(raw_ref: str) -> str:
        """Имя файла dds из пути: ui\\ui_icon.dds -> ui_icon.dds"""
        normalized = raw_ref.replace("\\", "/")
        return normalized.rsplit("/", 1)[-1] or raw_ref
        
    def _is_declaration(self, line: str, section: str) -> bool:
        """Проверяет, является ли строка объявлением секции."""
        stripped = line.strip()
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

        if self._is_declaration(line, section):
            return EdgeType.DECLARATION

        if self._is_inheritance(stripped, section):
            return EdgeType.INHERITANCE

        return EdgeType.LTX_REFERENCE

    def _is_inheritance(self, line: str, section: str) -> bool:
        """
        Проверяет наследование: [ребенок]:родитель1,секция,родитель2
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

        return text


# ------------------------------------------------------------ совместимость

def build_file_dependency_graph(forest):
    """
    Совместимость со старым API.
    """
    children = {}

    for p in forest.files:
        children[p] = list(forest.display_children.get(p, []))

    return {
        "roots": list(forest.roots),
        "files": list(forest.files),
        "children": children,
        "parent": dict(forest.node_parent),
        "subtree_counts": dict(forest.subtree_counts),
        "include_children": {
            k: set(v)
            for k, v in getattr(forest, "include_children", {}).items()
        },
        "include_parents": {
            k: set(v)
            for k, v in getattr(forest, "include_parents", {}).items()
        },
        "dltx_attachments": {
            k: set(v)
            for k, v in getattr(forest, "dltx_attachments", {}).items()
        },
    }