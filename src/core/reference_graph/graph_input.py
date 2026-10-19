"""GraphInput: версионированный входной снимок для фазы A reference_graph.

Зачем отдельный снимок (5.1.2, 5.1.4): фаза A обязана быть чистой функцией
одного неизменяемого входа. Живые запросы к сервисам прямо из индекса дали бы
перекос версий между запросами (дерево уже от нового снимка VFS, резолвер ещё
от старого). Ассемблер фиксирует все три источника одним штампом Versions и
отказывается собирать вход, если их VFS-снепшоты не совпадают.

reference_graph не знает классов сервисов: он видит только три порта ниже.
Порты реализуют либо сами сервисы, либо тонкие адаптеры в композиционном корне.
Порты обязаны отдавать кортежи в детерминированном порядке (порядок дерева /
порядок резолвера) — иначе снимок графа перестанет быть воспроизводимым.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import FrozenSet, Iterable, Mapping, Optional, Protocol, Tuple, runtime_checkable

from .models import ChainLink, Versions, RULES_VERSION
from .resolved_index import (LineView, RawCommentView, ResolvedSectionView,
                             ScriptFileView, SectionView, TreeFileView,
                             XmlFileView)


# --------------------------------------------------------------------------- #
# Порты источников: граница ответственности, а не реализации
# --------------------------------------------------------------------------- #

@runtime_checkable
class TreeStructurePort(Protocol):
    """Структурные запросы к дереву tree_builder через TreeQueryService.

    Граф берёт отсюда ТОЛЬКО имена файлов: свою enumerate-логику по файлам
    reference_graph не имеет (6.2: структурные запросы «показать все файлы»,
    «показать файлы определенного типа»).
    """
    def vfs_snapshot(self) -> str: ...
    def ltx_file_names(self) -> Tuple[str, ...]: ...
    def xml_file_names(self) -> Tuple[str, ...]: ...
    def script_file_names(self) -> Tuple[str, ...]: ...


@runtime_checkable
class ResolvedTreePort(Protocol):
    """Итоговое дерево Resolver4 через LtxResolverAdapter (стандартные модели, 6.3).

    Единственный источник финальных строк: TreeQueryService не разрешает
    значения секций (6.2), tree_builder тем более (6.1).
    """
    def vfs_snapshot(self) -> str: ...
    def resolver_version(self) -> str: ...
    def iter_tree_files(self) -> Iterable[TreeFileView]: ...
    def iter_resolved_sections(self) -> Iterable[ResolvedSectionView]: ...


@runtime_checkable
class ParsedRawPort(Protocol):
    """Сырьё из распарсенных структур FileCacheSystem (6.4).

    Комментарии отсутствуют в итоговом дереве (движок их не видит), поэтому
    low-связи из комментариев, а также xml-фрагменты и строки скриптов
    берутся отсюда, а не из резолвера.
    """
    def vfs_snapshot(self) -> str: ...
    def parser_version(self) -> str: ...
    def iter_raw_comments(self) -> Iterable[RawCommentView]: ...
    def xml_fragments(self, file_name: str) -> Tuple[str, ...]: ...
    def script_lines(self, file_name: str) -> Tuple[str, ...]: ...
    def raw_defined_section_names(self) -> FrozenSet[str]: ...


# --------------------------------------------------------------------------- #
# Неизменяемые вьюхи: структурно удовлетворяют протоколам resolved_index
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Line:
    """Строка итогового дерева. owner/disk_path — метаданные, графом не читаются."""
    key: str
    value: str
    owner: Optional[str] = None
    disk_path: Optional[str] = None
    chain: Tuple[ChainLink, ...] = ()


@dataclass(frozen=True)
class TreeSection:
    name: str
    lines: Tuple[Line, ...] = ()


@dataclass(frozen=True)
class TreeFile:
    """Файл итогового дерева: имя как в дереве, без диск-путей."""
    name: str
    sections: Tuple[TreeSection, ...] = ()


@dataclass(frozen=True)
class ResolvedSection:
    name: str
    parents: Tuple[str, ...] = ()
    keys: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class RawComment:
    file: str
    section: Optional[str]
    text: str


@dataclass(frozen=True)
class XmlFile:
    name: str
    fragments: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ScriptFile:
    name: str
    lines: Tuple[str, ...] = ()


# --------------------------------------------------------------------------- #
# Снимок: реализация протокола GraphInput
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class GraphInputSnapshot:
    """Один неизменяемый вход фазы A. Порядок кортежей = порядок источников."""
    tree_files: Tuple[TreeFile, ...]
    resolved_sections: Tuple[ResolvedSection, ...]
    raw_comments: Tuple[RawComment, ...]
    ltx_file_names: Tuple[str, ...]
    xml_files: Tuple[XmlFile, ...]
    script_files: Tuple[ScriptFile, ...]
    raw_defined: FrozenSet[str]
    stamp: Versions

    def iter_tree_files(self) -> Tuple[TreeFile, ...]:
        return self.tree_files

    def iter_resolved_sections(self) -> Tuple[ResolvedSection, ...]:
        return self.resolved_sections

    def iter_raw_comments(self) -> Tuple[RawComment, ...]:
        return self.raw_comments

    def iter_tree_file_names(self) -> Tuple[str, ...]:
        return self.ltx_file_names

    def iter_xml_files(self) -> Tuple[XmlFile, ...]:
        return self.xml_files

    def iter_script_files(self) -> Tuple[ScriptFile, ...]:
        return self.script_files

    def raw_defined_sections(self) -> FrozenSet[str]:
        return self.raw_defined

    def versions(self) -> Versions:
        return self.stamp


# --------------------------------------------------------------------------- #
# Сборка снимка
# --------------------------------------------------------------------------- #

class VersionSkewError(RuntimeError):
    """Источники дали данные от разных снимков VFS: вход недетерминирован (5.1.2)."""


def assemble_graph_input(tree: TreeStructurePort,
                         resolver: ResolvedTreePort,
                         raw: ParsedRawPort) -> GraphInputSnapshot:
    """Фиксирует три источника одним штампом Versions.

    Не пересобирает отстающий источник сам: при перекосе сообщает ошибкой,
    а пересборка — ответственность композиционного корня по сценарию 8.5
    («файловый кэш помечает старые данные недействительными; дерево, резолвер,
    граф связей пересчитываются по мере необходимости»).
    """
    vfs = tree.vfs_snapshot()
    resolver_vfs = resolver.vfs_snapshot()
    raw_vfs = raw.vfs_snapshot()
    if not (vfs == resolver_vfs == raw_vfs):
        raise VersionSkewError(
            f"vfs snapshots differ: tree={vfs}, resolver={resolver_vfs}, "
            f"raw={raw_vfs}; rebuild stale sources before assembling graph input"
        )

    return GraphInputSnapshot(
        tree_files=tuple(resolver.iter_tree_files()),
        resolved_sections=tuple(resolver.iter_resolved_sections()),
        raw_comments=tuple(raw.iter_raw_comments()),
        ltx_file_names=tuple(tree.ltx_file_names()),
        xml_files=tuple(XmlFile(name, tuple(raw.xml_fragments(name)))
                        for name in tree.xml_file_names()),
        script_files=tuple(ScriptFile(name, tuple(raw.script_lines(name)))
                           for name in tree.script_file_names()),
        raw_defined=frozenset(raw.raw_defined_section_names()),
        stamp=Versions(
            vfs_snapshot=vfs,
            parser=raw.parser_version(),
            resolver=resolver.resolver_version(),
            rules=RULES_VERSION,
        ),
    )