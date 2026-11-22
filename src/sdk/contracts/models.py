# -*- coding: utf-8 -*-
"""Общие DTO SDK: снимки, дерево, граф, события файлов."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Optional  # noqa: F401

from .forest import ForestRef, forest_ref


@dataclass(frozen=True)
class ProjectOverlay:
    """Единый слой правок проекта поверх VFS.

    ``source_files`` — черновики реальных файлов ``{path: text}``.
    ``resolved_edits`` — правки resolved-корней:
    ``{root_path: (original_text, current_text, line_sources)}``.
    """

    source_files: dict = field(default_factory=dict)
    resolved_edits: dict = field(default_factory=dict)
    new_files: frozenset = field(default_factory=frozenset)
    # Дешёвый токен состояния черновиков из ProjectService. Если он
    # передан, ревизия не пересчитывает тексты и line_sources.
    revision_token: str = ""

    def __post_init__(self):
        # Слой правок — снимок. После создания его нельзя менять:
        # иначе ключ кэша, уже посчитанный по revision, разойдётся с
        # данными, а один модуль сможет испортить overlay другому.
        object.__setattr__(
            self,
            "source_files",
            MappingProxyType(
                {
                    path: str(text)
                    for path, text in (self.source_files or {}).items()
                }
            ),
        )
        normalized_edits = {}
        for path, value in (self.resolved_edits or {}).items():
            original = str(value[0] or "")
            current = str(value[1] or "")
            sources = value[2] if len(value) > 2 else ()
            normalized_edits[path] = (
                original,
                current,
                tuple(sources or ()),
            )
        object.__setattr__(
            self,
            "resolved_edits",
            MappingProxyType(normalized_edits),
        )
        object.__setattr__(
            self,
            "new_files",
            frozenset(self.new_files or ()),
        )
        object.__setattr__(
            self,
            "revision_token",
            str(self.revision_token or ""),
        )
        object.__setattr__(
            self,
            "_revision",
            str(self.revision_token or "") or None,
        )

    def __reduce__(self):
        # MappingProxyType не picklable, а overlay передаётся в
        # процессы предсборки. Восстанавливаем его из обычных словарей.
        return (
            self.__class__,
            (
                dict(self.source_files),
                dict(self.resolved_edits),
                frozenset(self.new_files),
                self.revision_token,
            ),
        )

    def _compute_revision(self) -> str:
        digest = hashlib.sha1()

        for path, text in sorted(
            self.source_files.items(),
            key=lambda item: str(item[0]).lower(),
        ):
            digest.update(str(path).lower().encode("utf-8", "replace"))
            digest.update(b"\0")
            digest.update(
                hashlib.sha1(
                    str(text).encode("utf-8", "replace")
                ).digest()
            )
            digest.update(b"\1")

        for path, value in sorted(
            self.resolved_edits.items(),
            key=lambda item: str(item[0]).lower(),
        ):
            digest.update(str(path).lower().encode("utf-8", "replace"))
            digest.update(b"\2")
            digest.update(
                hashlib.sha1(
                    str(value[0]).encode("utf-8", "replace")
                ).digest()
            )
            digest.update(b"\0")
            digest.update(
                hashlib.sha1(
                    str(value[1]).encode("utf-8", "replace")
                ).digest()
            )
            # Карта источников входит в ревизию по содержимому, а не по
            # длине: иначе смена владельца строки оставляла бы старый
            # resolved-кэш. Ревизия считается один раз на overlay.
            digest.update(b"\3")
            sources = value[2] if len(value) > 2 else ()
            for source in sources or ():
                if source is None:
                    digest.update(b"none")
                else:
                    for attr in (
                        "kind",
                        "display",
                        "path",
                        "line",
                        "open_raw",
                    ):
                        digest.update(
                            repr(getattr(source, attr, None)).encode(
                                "utf-8",
                                "replace",
                            )
                        )
                        digest.update(b"\0")
                digest.update(b"\1")
            digest.update(b"\1")

        for path in sorted(
            (self.new_files or ()),
            key=lambda item: str(item).lower(),
        ):
            digest.update(str(path).lower().encode("utf-8", "replace"))
            digest.update(b"\4")

        return digest.hexdigest()

    @property
    def revision(self) -> str:
        """Подпись снимка. Считается лениво и один раз на overlay."""
        if self._revision is None:
            object.__setattr__(
                self,
                "_revision",
                self._compute_revision(),
            )
        return self._revision

    def source_map(self) -> dict:
        return dict(self.source_files)

    def resolved_map(self) -> dict:
        return dict(self.resolved_edits)

@dataclass(frozen=True)
class SnapshotChanged:
    """Сменилось поколение снимка VFS."""

    snapshot_id: str
    generation: int = 0


@dataclass(frozen=True)
class TreeSummary:
    """Краткая сводка по построенному дереву."""

    roots: int
    total_files: int
    system_tree_files: int
    resolver_sections: Optional[int] = None
    xml_files: int = 0

@dataclass(frozen=True)
class TreeReady:
    """Лёгкий payload события ``ui.tree_ready``.

    Сам ``LtxForest`` живёт в ``WorkspaceSession`` платформы; подписчики
    получают стабильный ключ поколения и сводку, а лес — через
    ``ctx.ui.workspace_forest()``/``workspace``.
    """

    cache_id: str
    generation: int = 0
    summary: Optional[TreeSummary] = None

@dataclass(frozen=True)
class GraphMatch:
    """Одно совпадение в графе связей секции."""

    edge_type: str
    line_number: int
    line_text: str = ""
    # Второй участник связи: для наследования — имя родителя или
    # дочерней секции.
    related: Optional[str] = None
    # True — related является родителем (искомая секция от него
    # унаследована); False — related является дочерней секцией.
    related_is_parent: bool = False

@dataclass(frozen=True)
class GraphNode:
    """Узел графа связей в терминах SDK, а не внутренних моделей ядра."""

    display_name: str
    file_path: Optional[Path]
    first_match_line: int
    matches: tuple[GraphMatch, ...] = field(default_factory=tuple)
    raw_ref: Optional[str] = None
    resolved: bool = False
    # Полный путь к бинарному ресурсу (dds/ogf). file_path при этом
    # указывает на файл, где записана ссылка (icons_texture/visual).
    target_path: Optional[Path] = None
    # Реальный путь на диске для запуска внешней программы. Может
    # отличаться от target_path, если тот получен через VFS (MO2/usvfs)
    # или собран junction-ами.
    physical_path: Optional[Path] = None
    # True — physical_path является временной копией VFS-ресурса.
    materialized: bool = False
    # Объявление связанной секции (родителя): файл, строка и признак
    # того, что строка относится к результату резолва.
    related_file_path: Optional[Path] = None
    related_line: Optional[int] = None
    related_resolved: bool = False

    @property
    def matches_count(self) -> int:
        return len(self.matches)

    @property
    def edge_types(self) -> tuple[str, ...]:
        return tuple(sorted({m.edge_type for m in self.matches}))

    @property
    def primary_edge_type(self) -> str:
        """Тип связи первой строки совпадения (для маркера в окне)."""
        if self.matches:
            return self.matches[0].edge_type
        return "declaration"

    @property
    def related_name(self) -> Optional[str]:
        """Имя второго участника связи (родитель/дочерняя секция)."""
        for match in self.matches:
            related = getattr(match, "related", None)
            if related:
                return related
        return None

    @property
    def is_texture(self) -> bool:
        return "texture_reference" in self.edge_types

    @property
    def is_visual(self) -> bool:
        return "visual_reference" in self.edge_types

    @property
    def is_asset(self) -> bool:
        """Узел ссылается на бинарный ресурс (текстура/модель)."""
        return self.is_texture or self.is_visual

@dataclass(frozen=True)
class GraphEdgeStyle:
    """Подпись и цвет маркера связи, который добавляет модуль."""

    label: str
    color_key: Optional[str] = None

@dataclass(frozen=True)
class SectionGraphContribution:
    """Дополнительные связи от модуля и их оформление."""

    children: tuple[GraphNode, ...] = field(default_factory=tuple)
    lost_children: tuple[GraphNode, ...] = field(default_factory=tuple)
    edge_styles: Mapping = field(default_factory=dict)
    # edge_type -> callable(node): модуль сам открывает свои узлы.
    openers: Mapping = field(default_factory=dict)

    def __post_init__(self):
        # Модуль собирает contribution сам, но после возврата платформа
        # и другие потребители должны видеть неизменяемый снимок.
        object.__setattr__(
            self,
            "edge_styles",
            MappingProxyType(dict(self.edge_styles or {})),
        )
        object.__setattr__(
            self,
            "openers",
            MappingProxyType(dict(self.openers or {})),
        )

@dataclass(frozen=True)
class SectionGraph:
    """Граф связей секции."""

    section_name: str
    root: GraphNode
    children: tuple[GraphNode, ...] = field(default_factory=tuple)
    # Заполняется только в режиме VFS: связи, отсутствующие в итоговом дереве.
    lost_children: tuple[GraphNode, ...] = field(default_factory=tuple)

@dataclass(frozen=True)
class SectionGraphRequest:
    """Контекст для модулей, дополняющих граф связей секции."""

    forest: ForestRef
    section_name: str
    graph: SectionGraph
    vfs_mode: bool = False
    overlay: Optional[ProjectOverlay] = None

    def __post_init__(self):
        # Даже если вызывающий передал сырой LtxForest, модуль получает
        # только read-only handle.
        object.__setattr__(self, "forest", forest_ref(self.forest))

@dataclass(frozen=True)
class FileOpened:
    """Факт открытия файла в конкретной вкладке.

    ``request_id`` — id запроса из ``UiApi.current_file_request_id()``;
    ``0`` для открытий вне запроса.
    """

    path: Path
    tab_id: str = ""
    request_id: int = 0

@dataclass(frozen=True)
class FileOpenFailed:
    """Открытие файла не состоялось.

    ``request_id`` позволяет отменить ровно тот запрос, который упал.
    """

    path: Path
    request_id: int = 0
    reason: str = ""
