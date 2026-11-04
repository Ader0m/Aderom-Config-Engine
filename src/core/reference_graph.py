# -*- coding: utf-8 -*-
"""ReferenceGraphBuilder — построение графа связей по имени секции."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from .fs import (
    TEXT_FILE_SUFFIXES,
    normalize_path,
    read_text_safe,
    scan_files,
)


# ------------------------------------------------------------ типы

class EdgeType(Enum):
    DECLARATION = "declaration"
    INHERITANCE = "inheritance"
    LTX_REFERENCE = "ltx_reference"
    SCRIPT_REFERENCE = "script_reference"
    XML_REFERENCE = "xml_reference"
    TEXTURE_REFERENCE = "texture_reference"
    VISUAL_REFERENCE = "visual_reference"


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
    # True — file_path/line относятся к результату резолва;
    # False — к исходному файлу (VFS/скрипты).
    resolved: bool = False
    # Полный путь к бинарному ресурсу (dds/ogf), если его удалось
    # разрешить от корня VFS. file_path при этом остаётся файлом,
    # в котором записана ссылка (icons_texture/visual).
    target_path: Optional[Path] = None

@dataclass
class SectionGraph:
    section_name: str
    root: GraphNode
    children: list[GraphNode] = field(default_factory=list)
    # Только для режима VFS: связи, которых нет в итоговом дереве.
    lost_children: list[GraphNode] = field(default_factory=list)


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

    Обычный режим работает только с готовым деревом (LtxForest) от
    TreeBuilder. Для .ltx файлов использует пути из леса, для .script
    сканирует scan_dir леса, для .xml — XmlResolver, если передан
    ``xml_provider``, иначе сырые XML-файлы леса.

    Режим ``vfs_mode`` игнорирует фильтр LtxForest и сканирует все
    текстовые файлы под ``forest.scan_dir``. Это диагностический режим:
    он не запускает резолвер, а сравнивает сырое дерево с сырым.
    """

    def __init__(self, cache=None):
        self.cache = cache or DisabledCache()

    def build_for_section(
        self,
        forest,
        section_name: str,
        vfs_mode: bool = False,
        resolved_provider=None,
        xml_provider=None,
        log=None,
    ) -> SectionGraph:
        if vfs_mode:
            # VFS-режим — диагностический: сравниваем сырое дерево с
            # сырым деревом, не запуская резолв.
            return self.build_for_section_vfs(
                forest,
                section_name,
                log=log,
            )

        section = section_name.lower().strip()

        # Основной режим — поиск по финальным файлам резолвера.
        if resolved_provider is not None:
            graph = self._build_resolved(
                forest,
                section,
                resolved_provider,
                xml_provider=xml_provider,
                log=log,
            )
            if graph is not None:
                return graph
            if log:
                log("resolved graph недоступен — fallback на исходные файлы")

        return self._build_raw(forest, section, log=log)

    def _build_raw(self, forest, section: str, log=None) -> SectionGraph:
        """Старый режим: поиск по исходным .ltx + .script файлам."""
        # 1. Ищем объявление секции + ресурсы в теле секции
        root_node, assets = self._find_declaration(forest, section)
        if root_node is None:
            raise ValueError(f"Секция '{section}' не найдена в дереве")

        children = []

        # 1.1. Ассеты секции: icons_texture = * и visual = *
        for kind, raw_ref, asset_match in assets:
            children.append(self._make_asset_node(
                forest,
                kind,
                raw_ref,
                asset_match,
                file_path=root_node.file_path,
                resolved=False,
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
        self._append_script_children(children, forest, section)

        # 3.1. XML в raw-режиме: нужен для корректного сравнения VFS
        # «потерянных» связей, иначе XML всегда выглядит потерянным.
        self._append_xml_raw_children(children, forest, section)

        # 4. XML — заглушка в старом смысле больше не нужна

        return SectionGraph(
            section_name=section,
            root=root_node,
            children=children,
        )

    def _build_resolved(self, forest, section, resolved_provider,
                        xml_provider=None, log=None):
        """Поиск по итоговым файлам резолвера (LTX-корням)."""
        from .resolve_targets import resolved_ltx_roots

        roots = resolved_ltx_roots(forest)
        if not roots and xml_provider is None:
            return None

        items = []
        total = len(roots)

        for i, root in enumerate(roots, 1):
            try:
                result = resolved_provider(root)
            except Exception as exc:
                if log:
                    log("resolved graph: %s: %s" % (root.name, exc))
                continue

            if result is None:
                continue

            if isinstance(result, tuple):
                text = result[0]
                line_sources = result[1] if len(result) > 1 else []
            else:
                text = result
                line_sources = []

            if not text:
                continue

            items.append((root, text, line_sources or []))
            if log and (i == 1 or i == total or i % 25 == 0):
                log("resolved graph: %d/%d" % (i, total))

        if not items and xml_provider is None:
            return None

        root_node, assets = self._find_declaration_in_items(
            [(path, text) for path, text, _sources in items],
            section,
            resolved=True,
        )
        children = []

        if root_node is not None:
            for kind, raw_ref, asset_match in assets:
                children.append(self._make_asset_node(
                    forest,
                    kind,
                    raw_ref,
                    asset_match,
                    file_path=root_node.file_path,
                    resolved=True,
                ))

        for root, text, line_sources in items:
            matches = self._scan_ltx_text(text, section)

            if root_node is not None and root == root_node.file_path:
                # Объявление уже показано корневым узлом; остальные
                # вхождения в том же итоговом файле оставляем детьми.
                matches = [
                    match for match in matches
                    if not (
                        match.line_number == root_node.first_match_line
                        and match.edge_type == EdgeType.DECLARATION
                    )
                ]

            if not matches:
                continue

            display = self._owner_label(
                line_sources,
                matches[0].line_number,
                root.name,
            )
            children.append(GraphNode(
                display_name=display,
                file_path=root,
                first_match_line=matches[0].line_number,
                matches=matches,
                resolved=True,
            ))

        # Скрипты не проходят через LTX-резолвер — их ищем по исходникам.
        self._append_script_children(children, forest, section)

        # XML проходит через XmlResolver (include + DXML).
        if xml_provider is not None:
            self._append_xml_resolved_children(
                children,
                forest,
                section,
                xml_provider,
                log=log,
            )

        if root_node is None:
            # Секция может встречаться только в XML.
            for index, node in enumerate(children):
                if node.resolved and node.file_path is not None:
                    root_node = children.pop(index)
                    root_node = GraphNode(
                        display_name=section,
                        file_path=root_node.file_path,
                        first_match_line=root_node.first_match_line,
                        matches=root_node.matches,
                        raw_ref=root_node.raw_ref,
                        resolved=True,
                    )
                    break

        if root_node is None:
            return None

        return SectionGraph(
            section_name=section,
            root=root_node,
            children=children,
        )

    def _append_xml_resolved_children(self, children, forest, section,
                                      provider, log=None):
        paths = list(getattr(forest, "xml_files", ()) or ())
        total = len(paths)

        for i, path in enumerate(paths, 1):
            try:
                result = provider(path)
            except Exception as exc:
                if log:
                    log("resolved xml: %s: %s" % (Path(path).name, exc))
                continue

            if result is None:
                continue

            if isinstance(result, tuple):
                text = result[0]
                line_sources = result[1] if len(result) > 1 else []
            else:
                text = result
                line_sources = []

            if text:
                matches = self._scan_xml_text(text, section)
                if matches:
                    display = self._owner_label(
                        line_sources,
                        matches[0].line_number,
                        Path(path).name,
                    )
                    children.append(GraphNode(
                        display_name=display,
                        file_path=Path(path),
                        first_match_line=matches[0].line_number,
                        matches=matches,
                        resolved=True,
                    ))

            if log and total and (i == 1 or i == total or i % 50 == 0):
                log("resolved xml: %d/%d" % (i, total))

    def _append_xml_raw_children(self, children, forest, section):
        for path in (getattr(forest, "xml_files", ()) or ()):
            path = Path(path)
            matches = self._scan_xml_file(path, section)
            if matches:
                children.append(GraphNode(
                    display_name=path.name,
                    file_path=path,
                    first_match_line=matches[0].line_number,
                    matches=matches,
                ))

    def _append_script_children(self, children, forest, section):
        scan_dir = Path(forest.scan_dir)
        if not scan_dir.is_dir():
            return

        for script_path in scan_files(scan_dir, {".script"}):
            matches = self._scan_script_file(script_path, section)
            if matches:
                children.append(GraphNode(
                    display_name=script_path.name,
                    file_path=script_path,
                    first_match_line=matches[0].line_number,
                    matches=matches,
                ))

    @staticmethod
    def _owner_label(line_sources, line_number, fallback):
        """Имя исходного файла-владельца строки итогового резолва."""
        try:
            if line_sources and 1 <= line_number <= len(line_sources):
                source = line_sources[line_number - 1]
                if getattr(source, "kind", None) == "original":
                    return fallback
                display = getattr(source, "display", None)
                if display:
                    return display
                if isinstance(source, tuple) and source:
                    return source[0]
                if isinstance(source, str) and source:
                    return source
        except Exception:
            pass
        return fallback

    # ------------------------------------------------------------ режим VFS

    def build_for_section_vfs(self, forest, section_name: str,
                              log=None) -> SectionGraph:
        """Построить граф по всем текстовым файлам VFS, без фильтра LtxForest.

        Сравнение «потерянных» связей идёт с сырым деревом
        (:meth:`_build_raw`), а не с результатом резолвера: VFS-режим
        не должен запускать Resolver4/XmlResolver по всему лесу.

        В ``lost_children`` попадают только совпадения, которых нет в
        обычном (итоговом) графе той же секции.
        """
        section = section_name.lower().strip()
        files = scan_files(Path(forest.scan_dir), TEXT_FILE_SUFFIXES)

        try:
            final_graph = self._build_raw(forest, section)
        except ValueError:
            final_graph = None

        # Если секция есть в итоговом дереве, держим его объявление
        # первым: тогда перекрытые объявления из VFS попадут в children
        # и смогут быть показаны как «потерянные».
        if final_graph is not None:
            final_root = normalize_path(final_graph.root.file_path)
            files = [final_root] + [f for f in files if f != final_root]

        root_node, assets = self._find_declaration_in_files(files, section)
        if root_node is None:
            raise ValueError(f"Секция '{section}' не найдена во VFS")

        children = []
        for kind, raw_ref, asset_match in assets:
            children.append(self._make_asset_node(
                forest,
                kind,
                raw_ref,
                asset_match,
                file_path=root_node.file_path,
                resolved=False,
            ))

        for file_path in files:
            if file_path == root_node.file_path:
                continue

            matches = self._scan_vfs_file(file_path, section)
            if matches:
                children.append(GraphNode(
                    display_name=file_path.name,
                    file_path=file_path,
                    first_match_line=matches[0].line_number,
                    matches=matches,
                ))

        graph = SectionGraph(
            section_name=section,
            root=root_node,
            children=children,
        )

        graph.lost_children = self._lost_children(graph, final_graph)
        return graph

    def _scan_vfs_file(self, file_path: Path, section: str) -> list[MatchInfo]:
        """Просканировать один текстовый файл VFS."""
        suffix = file_path.suffix.lower()

        if suffix == ".ltx":
            return self._scan_ltx_file(file_path, section)
        if suffix in (".script", ".lua"):
            return self._scan_script_file(file_path, section)
        if suffix == ".xml":
            return self._scan_xml_file(file_path, section)

        return []

    def _scan_xml_file(self, file_path: Path, section: str) -> list[MatchInfo]:
        """Сканирует .xml на упоминания секции."""
        text = self._read_file(file_path)
        if not text:
            return []
        return self._scan_xml_text(text, section)

    def _scan_xml_text(self, text: str, section: str) -> list[MatchInfo]:
        """Сканирует исходный или итоговый XML-текст."""
        matches = []
        for line_num, line in enumerate(text.splitlines(), 1):
            if section in line.lower():
                matches.append(MatchInfo(
                    edge_type=EdgeType.XML_REFERENCE,
                    line_number=line_num,
                    line_text=line,
                ))
        return matches

    def _lost_children(self, vfs_graph: SectionGraph, final_graph: SectionGraph):
        """Оставить только те совпадения VFS, которых нет в итоговом графе."""
        final_keys = set()

        # XML сравниваем по файлу, а не по строке: разные представления
        # (резолв/DXML) меняют форматирование и номера строк. Если файл
        # уже найден в итоговом графе, он не потерян.
        final_xml_files = set()

        if final_graph is not None:
            if final_graph.root.file_path is not None:
                root_path = normalize_path(final_graph.root.file_path)
                if root_path.suffix.lower() == ".xml":
                    final_xml_files.add(root_path)
            for match in final_graph.root.matches:
                final_keys.add(self._match_key(final_graph.root.file_path, match))
            for node in final_graph.children:
                if node.file_path is not None:
                    node_path = normalize_path(node.file_path)
                    if node_path.suffix.lower() == ".xml":
                        final_xml_files.add(node_path)
                for match in node.matches:
                    final_keys.add(self._match_key(node.file_path, match))

        lost = []
        for node in vfs_graph.children:
            if (
                node.file_path is not None
                and normalize_path(node.file_path) in final_xml_files
            ):
                continue

            matches = [
                match
                for match in node.matches
                if self._match_key(node.file_path, match) not in final_keys
            ]
            if not matches:
                continue

            lost.append(GraphNode(
                display_name=node.display_name,
                file_path=node.file_path,
                first_match_line=matches[0].line_number,
                matches=matches,
                raw_ref=node.raw_ref,
                target_path=getattr(node, "target_path", None),
            ))

        return lost

    @staticmethod
    def _match_key(file_path, match: MatchInfo):
        path = normalize_path(file_path) if file_path is not None else None
        return (
            path,
            int(match.line_number),
            match.edge_type,
            match.line_text.strip(),
        )

    # ------------------------------------------------------------ поиск объявления

    def _find_declaration(self, forest, section: str):
        """
        Ищем объявление секции [секция] или [секция]:... в файлах леса.
        Возвращает ``(root_node, assets)``, где ``assets`` — список ссылок
        на ресурсы секции (``icons_texture``/``visual``).
        """
        return self._find_declaration_in_files(forest.files, section)

    def _find_declaration_in_files(self, files, section: str):
        """Ищет объявление секции в произвольном наборе файлов."""
        items = [
            (file_path, self._read_file(file_path))
            for file_path in files
        ]
        return self._find_declaration_in_items(items, section)

    def _find_declaration_in_items(
        self,
        items,
        section: str,
        resolved: bool = False,
    ):
        """Ищет объявление секции в парах (file_path, text).

        Возвращает ``(root_node, assets)``. ``assets`` — список
        ``(kind, raw_ref, MatchInfo)`` для ключей ``icons_texture`` и
        ``visual`` в теле найденной секции.
        """
        for file_path, text in items:
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
                        resolved=resolved,
                    )
                    assets = self._find_section_assets(lines, idx + 1)
                    return root, assets

        return None, []

    def _find_section_assets(self, lines, start_idx):
        """
        Читает тело секции от строки объявления до следующего заголовка [...]
        и собирает ссылки на ресурсы: icons_texture (dds) и visual (ogf).
        Возвращает список ``(kind, raw_ref, MatchInfo)``.
        """
        assets = []

        for i in range(start_idx, len(lines)):
            s = lines[i].strip()

            if not s or s.startswith(";"):
                continue

            # начало следующей секции — тело закончилось
            if s.startswith("[") or s.startswith("![") or s.startswith("@[") or s.startswith("!!["):
                break

            if "=" not in s:
                continue

            key, raw = s.split("=", 1)
            key = key.strip().lower()
            raw = raw.strip()
            if not raw:
                continue

            if key == "icons_texture":
                kind = "texture"
                edge_type = EdgeType.TEXTURE_REFERENCE
            elif key == "visual":
                kind = "visual"
                edge_type = EdgeType.VISUAL_REFERENCE
            else:
                continue

            assets.append((
                kind,
                raw,
                MatchInfo(
                    edge_type=edge_type,
                    line_number=i + 1,
                    line_text=lines[i],
                ),
            ))

        return assets

    def _find_icons_texture(self, lines, start_idx):
        """Совместимость: первая ссылка icons_texture в теле секции."""
        for kind, raw_ref, match in self._find_section_assets(lines, start_idx):
            if kind == "texture":
                return raw_ref, match
        return None, None

    @staticmethod
    def _texture_display_name(raw_ref: str) -> str:
        """Имя файла dds из пути: ui\\ui_icon.dds -> ui_icon.dds"""
        normalized = raw_ref.replace("\\", "/")
        return normalized.rsplit("/", 1)[-1] or raw_ref

    # ------------------------------------------------------------ ассеты секции

    def _make_asset_node(self, forest, kind, raw_ref, match,
                         file_path, resolved=False) -> GraphNode:
        """Узел ресурса секции: текстура (.dds) или визуальная модель (.ogf)."""
        target = self._resolve_asset_path(forest, kind, raw_ref)
        return GraphNode(
            display_name=self._asset_display_name(kind, raw_ref, target),
            file_path=file_path,
            first_match_line=match.line_number,
            matches=[match],
            raw_ref=raw_ref,
            resolved=resolved,
            target_path=target,
        )

    @staticmethod
    def _asset_display_name(kind, raw_ref, target=None) -> str:
        if target is not None:
            return target.name
        if kind == "texture":
            return ReferenceGraphBuilder._texture_display_name(raw_ref)
        normalized = str(raw_ref).replace("\\", "/")
        return normalized.rsplit("/", 1)[-1] or str(raw_ref)

    @staticmethod
    def _resolve_asset_path(forest, kind, raw_ref):
        """Полный путь к dds/ogf относительно корня VFS.

        ``forest.scan_dir`` — каталог configs (например,
        ``<root>/gamedata/configs``). Ресурсы игры лежат рядом:
        ``textures`` и ``meshes``. Проверяем несколько баз, чтобы
        покрыть и VFS-корень, и отдельный каталог мода.
        """
        scan_dir = getattr(forest, "scan_dir", None)
        if not scan_dir:
            return None

        raw = str(raw_ref or "").strip().strip('"').replace("\\", "/")
        if not raw:
            return None

        suffix = ".dds" if kind == "texture" else ".ogf"
        subdir = "textures" if kind == "texture" else "meshes"

        p = Path(raw)
        if p.is_absolute():
            candidates = [p]
        else:
            if not p.suffix:
                raw += suffix
            has_subdir = raw.lower().startswith(subdir + "/")

            bases = []
            scan = Path(scan_dir)
            for base in (scan, scan.parent, scan.parent.parent):
                for candidate_base in (base, base / "gamedata"):
                    if candidate_base not in bases:
                        bases.append(candidate_base)

            candidates = []
            for base in bases:
                if has_subdir:
                    candidates.append(base / raw)
                else:
                    candidates.append(base / subdir / raw)
                    candidates.append(base / raw)

        for candidate in candidates:
            try:
                if candidate.is_file():
                    return normalize_path(candidate)
            except OSError:
                continue

        return None

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
        return self._scan_ltx_text(text, section)

    def _scan_ltx_text(self, text: str, section: str) -> list[MatchInfo]:
        """Сканирует текст LTX (исходный или итоговый) на совпадения."""
        matches = []
        for line_num, line in enumerate(text.splitlines(), 1):
            if section not in line.lower():
                continue

            # Ссылки на ресурсы секции (icons_texture/visual) показаны
            # отдельными узлами и не являются LTX-ссылками на секцию,
            # даже если имя секции встречается в пути ассета.
            if self._is_asset_line(line):
                continue

            edge_type = self._classify_ltx_line(line, section)
            matches.append(MatchInfo(
                edge_type=edge_type,
                line_number=line_num,
                line_text=line,
            ))

        return matches

    @staticmethod
    def _is_asset_line(line: str) -> bool:
        s = line.strip()
        if "=" not in s:
            return False
        key = s.split("=", 1)[0].strip().lower()
        return key in ("icons_texture", "visual")

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
            text = read_text_safe(file_path)
        except Exception:
            text = ""

        self.cache.set(("file", str(file_path)), text)
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