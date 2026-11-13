# -*- coding: utf-8 -*-
"""ReferenceGraphBuilder — построение графа связей по имени секции."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from core.tools.fs import (
    TEXT_FILE_SUFFIXES,
    normalize_path,
    read_text_safe,
    scan_files,
)
from core.tools.vfs import resolve_asset_path, resolve_physical_path


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
    # Второй участник связи: для наследования — имя родителя
    # (если искали дочернюю секцию) или имя дочерней секции
    # (если искали родителя).
    related: Optional[str] = None
    # True — related является родителем искомой секции, поэтому
    # двойной клик должен открыть объявление родителя.
    related_is_parent: bool = False


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
    # Реальный путь для запуска внешнего приложения. target_path —
    # VFS-путь (идентичность/отображение), physical_path — путь на
    # диске после раскрытия junction/usvfs или временная копия.
    physical_path: Optional[Path] = None
    # Объявление связанной секции (родителя) — куда переходить по
    # двойному клику на строке [Наследование].
    related_file_path: Optional[Path] = None
    related_line: Optional[int] = None
    related_resolved: bool = False

    @property
    def related_name(self) -> Optional[str]:
        """Имя второго участника связи (родитель/дочерняя секция)."""
        for match in self.matches:
            related = getattr(match, "related", None)
            if related:
                return related
        return None

    @property
    def primary_edge_type(self) -> str:
        """Тип связи первой строки совпадения."""
        if self.matches:
            return self.matches[0].edge_type.value
        return "declaration"

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

        # 1.2. Индекс объявлений нужен, чтобы по двойному клику на
        # родителе открыть именно его файл/строку.
        declaration_index = {
            section: (root_node.file_path, root_node.first_match_line)
        }
        pending = []
        for file_path in forest.files:
            text = self._read_file(file_path)
            self._index_declarations(text, file_path, declaration_index)
            if file_path == root_node.file_path:
                continue
            matches = self._scan_ltx_text(text, section)
            pending.append((file_path, matches))

        # 1.3. Родители самой секции: [секция]:p1,p2
        self._append_ltx_matches(
            children,
            root_node.file_path,
            self._root_inheritance_matches(root_node, section),
            resolved=False,
            declaration_index=declaration_index,
            related_resolved=False,
        )

        # 2. Остальные .ltx файлы из леса
        for file_path, matches in pending:
            self._append_ltx_matches(
                children,
                file_path,
                matches,
                declaration_index=declaration_index,
                related_resolved=False,
            )

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
        from core.resolver.prebuild import resolved_ltx_roots

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

        declaration_index = {}
        for path, text, _sources in items:
            self._index_declarations(text, path, declaration_index)

        if root_node is not None:
            declaration_index[section] = (
                root_node.file_path,
                root_node.first_match_line,
            )
            for kind, raw_ref, asset_match in assets:
                children.append(self._make_asset_node(
                    forest,
                    kind,
                    raw_ref,
                    asset_match,
                    file_path=root_node.file_path,
                    resolved=True,
                ))
            # Родители самой секции из итогового текста резолва.
            self._append_ltx_matches(
                children,
                root_node.file_path,
                self._root_inheritance_matches(root_node, section),
                resolved=True,
                declaration_index=declaration_index,
                related_resolved=True,
            )

        for root, text, line_sources in items:
            matches = self._scan_ltx_text(text, section)

            if root_node is not None and root == root_node.file_path:
                # Объявление и его родители уже показаны корневым узлом
                # и отдельными связями; остальные вхождения в том же
                # итоговом файле оставляем детьми.
                matches = [
                    match for match in matches
                    if match.line_number != root_node.first_match_line
                ]

            if not matches:
                continue

            display = self._owner_label(
                line_sources,
                matches[0].line_number,
                root.name,
            )
            self._append_ltx_matches(
                children,
                root,
                matches,
                resolved=True,
                display_name=display,
                declaration_index=declaration_index,
                related_resolved=True,
            )

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

    # ------------------------------------------------------------ наследование

    def _root_inheritance_matches(self, root: GraphNode, section: str):
        """Связи с родителями из заголовка ``[секция]:p1,p2``."""
        if not root.matches:
            return []
        line = root.matches[0].line_text
        child, parents = self._parse_inheritance(line)
        if child != section:
            return []
        return [
            MatchInfo(
                edge_type=EdgeType.INHERITANCE,
                line_number=root.first_match_line,
                line_text=line,
                related=parent,
                related_is_parent=True,
            )
            for parent in parents
        ]

    def _append_ltx_matches(self, children, file_path, matches,
                            resolved=False, display_name=None,
                            declaration_index=None,
                            related_resolved=False):
        """Добавить совпадения, развернув наследование в отдельные связи.

        Обычные совпадения по-прежнему агрегируются в один узел файла,
        а каждый родитель из ``[секция]:p1,p2`` становится отдельной
        строкой ``[Наследование]``. Если известен ``declaration_index``,
        для родителя сохраняется файл и строка его объявления.
        """
        if not matches:
            return

        regular = []
        for match in matches:
            if match.edge_type == EdgeType.INHERITANCE:
                related = getattr(match, "related", None) or file_path.name
                related_target = {}
                if (
                    getattr(match, "related_is_parent", False)
                    and declaration_index
                ):
                    location = declaration_index.get(
                        str(related).lower()
                    )
                    if location is not None:
                        related_target = {
                            "related_file_path": location[0],
                            "related_line": location[1],
                            "related_resolved": related_resolved,
                        }
                children.append(GraphNode(
                    display_name=related,
                    file_path=file_path,
                    first_match_line=match.line_number,
                    matches=[match],
                    resolved=resolved,
                    **related_target,
                ))
            else:
                regular.append(match)

        if regular:
            children.append(GraphNode(
                display_name=display_name or file_path.name,
                file_path=file_path,
                first_match_line=regular[0].line_number,
                matches=regular,
                resolved=resolved,
            ))

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

        # Родители самой секции из сырого заголовка [секция]:p1,p2.
        declaration_index = {
            section: (root_node.file_path, root_node.first_match_line)
        }
        pending = []
        for file_path in files:
            text = self._read_file(file_path)
            self._index_declarations(text, file_path, declaration_index)
            if file_path == root_node.file_path:
                continue
            matches = self._scan_text(file_path, text, section)
            pending.append((file_path, matches))

        self._append_ltx_matches(
            children,
            root_node.file_path,
            self._root_inheritance_matches(root_node, section),
            resolved=False,
            declaration_index=declaration_index,
            related_resolved=False,
        )

        for file_path, matches in pending:
            self._append_ltx_matches(
                children,
                file_path,
                matches,
                declaration_index=declaration_index,
                related_resolved=False,
            )

        graph = SectionGraph(
            section_name=section,
            root=root_node,
            children=children,
        )

        # Ядро отдаёт один список — все сырые связи VFS. Разделение
        # на «живые» и «потерянные» делает вызывающая сторона,
        # сравнивая этот список с уже построенным resolved-графом.
        return graph

    def _scan_vfs_file(self, file_path: Path, section: str) -> list[MatchInfo]:
        """Просканировать один текстовый файл VFS."""
        text = self._read_file(file_path)
        if not text:
            return []
        return self._scan_text(file_path, text, section)

    def _scan_text(self, file_path: Path, text: str,
                   section: str) -> list[MatchInfo]:
        """Просканировать уже прочитанный текст по типу файла."""
        suffix = file_path.suffix.lower()

        if suffix == ".ltx":
            return self._scan_ltx_text(text, section)
        if suffix in (".script", ".lua"):
            return self._scan_script_text(text, section)
        if suffix == ".xml":
            return self._scan_xml_text(text, section)

        return []

    def _scan_xml_file(self, file_path: Path, section: str) -> list[MatchInfo]:
        """Сканирует .xml на упоминания секции."""
        return self._scan_vfs_file(file_path, section)

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
        # .ogf-модели обычно упакованы в игровые архивы и не открываются
        # внешней программой, поэтому физический путь для них не ищем.
        physical = (
            resolve_physical_path(target)
            if target is not None and kind != "visual"
            else None
        )
        return GraphNode(
            display_name=self._asset_display_name(kind, raw_ref, target),
            file_path=file_path,
            first_match_line=match.line_number,
            matches=[match],
            raw_ref=raw_ref,
            resolved=resolved,
            target_path=target,
            physical_path=physical,
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
        """Полный путь к dds/ogf относительно корня VFS."""
        return resolve_asset_path(
            getattr(forest, "scan_dir", None),
            kind,
            raw_ref,
        )

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
        return self._scan_vfs_file(file_path, section)

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

            child, parents = self._parse_inheritance(line)

            if child is not None:
                if child == section:
                    # [секция]:родитель1,родитель2 — сама секция
                    # объявлена здесь; каждый родитель — отдельная связь.
                    matches.append(MatchInfo(
                        edge_type=EdgeType.DECLARATION,
                        line_number=line_num,
                        line_text=line,
                    ))
                    for parent in parents:
                        matches.append(MatchInfo(
                            edge_type=EdgeType.INHERITANCE,
                            line_number=line_num,
                            line_text=line,
                            related=parent,
                            related_is_parent=True,
                        ))
                    continue

                if section in parents:
                    # [ребенок]:родитель1,секция,родитель2 — искали
                    # родителя; показываем связь с ребенком.
                    matches.append(MatchInfo(
                        edge_type=EdgeType.INHERITANCE,
                        line_number=line_num,
                        line_text=line,
                        related=child,
                    ))
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
        _child, parents = self._parse_inheritance(line)
        return section in parents

    @staticmethod
    def _parse_inheritance(line: str):
        """Разобрать заголовок ``[ребенок]:родитель1,родитель2``.

        Возвращает ``(child, parents)`` или ``(None, [])``, если строка
        не является заголовком с наследованием. Имена приводятся к
        нижнему регистру; комментарии после списка отбрасываются.
        """
        stripped = line.strip()
        if not stripped.startswith("["):
            return None, []

        bracket_end = stripped.find("]:")
        if bracket_end == -1:
            return None, []

        child = stripped[1:bracket_end].strip().lower()
        parents_part = stripped[bracket_end + 2:]
        parents_part = parents_part.split(";", 1)[0]
        parents_part = parents_part.split("//", 1)[0]
        parents = [
            parent.strip().lower()
            for parent in parents_part.split(",")
            if parent.strip()
        ]
        return child, parents

    @staticmethod
    def _index_declarations(text: str, file_path, index: dict):
        """Добавить в индекс объявления секций из текста.

        Индекс: ``имя секции -> (файл, строка)``. Первое объявление
        побеждает — как и в :meth:`_find_declaration_in_items`.
        """
        if not text:
            return
        for line_num, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            while stripped.startswith(("!", "@")):
                stripped = stripped[1:].lstrip()
            if not stripped.startswith("["):
                continue
            end = stripped.find("]")
            if end == -1:
                continue
            name = stripped[1:end].strip().lower()
            if name and name not in index:
                index[name] = (file_path, line_num)

    # ------------------------------------------------------------ сканирование .script

    def _scan_script_file(self, file_path: Path, section: str) -> list[MatchInfo]:
        """Сканирует .script файл на совпадения."""
        return self._scan_vfs_file(file_path, section)

    def _scan_script_text(self, text: str, section: str) -> list[MatchInfo]:
        """Сканирует текст .script/.lua на совпадения."""
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