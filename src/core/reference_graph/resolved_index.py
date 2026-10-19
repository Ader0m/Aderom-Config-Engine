# -*- coding: utf-8 -*-
"""Фаза A reference_graph: индекс упоминаний по итоговому дереву + сырые low/xml/script.

Входной контракт к Resolver4/адаптеру (подтверждён):
- итоговое дерево: файл -> секции -> строки (ключ, значение);
- строка опционально несёт owner (имя файла) и путь до реального файла на диске:
  они уходят в SourceMeta и графом НЕ читаются («не используем, но не теряем»);
- файловый узел именуется по файлу дерева, в котором упоминание найдено при обходе;
- комментариев в итоговом дереве нет — low из комментариев берётся из сырых файлов;
- имена секций нормализованы (нижний регистр).

Типы связей, собираемые фазой A:
- KEY/VALUE    — токен-совпадения в итоговом дереве (high/medium);
- DEFINITION   — файл владеет итоговыми строками секции (объявление/переопределение),
                 всегда high;
- COMMENT/FILENAME — low, из сырых данных (движок их не видит, видит человек);
- XML/SCRIPT   — сырой скан: итогового дерева для этих форматов нет.

Индекс строится один раз на снимок VFS; кэшируется FileCacheSystem по ключу Versions.
Построение — чистая функция от GraphInput: одинаковый вход даёт одинаковый индекс.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import (Dict, FrozenSet, Iterable, List, Mapping, Optional,
                    Protocol, Tuple, runtime_checkable)

from .classifier import classify
from .models import Mention, MentionOrigin, SourceMeta, Versions
from .tokenizer import MatchKind, normalize_name, tokenize

EMPTY_KEYS: Mapping[str, str] = {}


# --------------------------------------------------------------------------- #
# Входные протоколы: граф не зависит от конкретных классов Resolver4
# --------------------------------------------------------------------------- #

@runtime_checkable
class LineView(Protocol):
    key: str
    value: str
    owner: Optional[str]          # итоговый владелец строки (имя файла)
    disk_path: Optional[str]      # путь до реального файла на диске
    chain: Tuple[object, ...]     # цепочка переопределений


@runtime_checkable
class SectionView(Protocol):
    name: str
    lines: Iterable[LineView]


@runtime_checkable
class TreeFileView(Protocol):
    name: str                     # имя файла в итоговом дереве
    sections: Iterable[SectionView]


@runtime_checkable
class ResolvedSectionView(Protocol):
    name: str
    parents: Tuple[str, ...]      # финальные родители (после :!, !, !!)
    keys: Mapping[str, str]       # финальные ключ-значения


@runtime_checkable
class RawCommentView(Protocol):
    file: str
    section: Optional[str]
    text: str


@runtime_checkable
class XmlFileView(Protocol):
    name: str
    fragments: Iterable[str]      # значения атрибутов / текст узлов для сырого скана


@runtime_checkable
class ScriptFileView(Protocol):
    name: str
    lines: Iterable[str]


@runtime_checkable
class GraphInput(Protocol):
    def iter_tree_files(self) -> Iterable[TreeFileView]: ...
    def iter_resolved_sections(self) -> Iterable[ResolvedSectionView]: ...
    def iter_raw_comments(self) -> Iterable[RawCommentView]: ...
    def iter_tree_file_names(self) -> Iterable[str]: ...
    def iter_xml_files(self) -> Iterable[XmlFileView]: ...
    def iter_script_files(self) -> Iterable[ScriptFileView]: ...
    def raw_defined_sections(self) -> FrozenSet[str]: ...
    def versions(self) -> Versions: ...


# --------------------------------------------------------------------------- #
# Разрешение токена в цель
# --------------------------------------------------------------------------- #

def resolve_target(token: str, known: FrozenSet[str]) -> Optional[Tuple[str, MatchKind]]:
    """Токен -> (целевая секция, вид совпадения) по правилам пункта 3.

    EXACT    — токен равен имени секции;
    PREFIXED — токен вида <префикс>_<секция>: секция является самым длинным
               суффиксом токена по границам '_';
    None     — подстрока без границы ('raid_material_10') и форма <секция>_<суффикс>.
    """
    if token in known:
        return token, MatchKind.EXACT
    parts = token.split("_")
    for split in range(1, len(parts)):
        suffix = "_".join(parts[split:])
        if suffix in known:
            return suffix, MatchKind.PREFIXED
    return None


# --------------------------------------------------------------------------- #
# Результат фазы A
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ResolvedMentionIndex:
    mentions: Mapping[str, Tuple[Mention, ...]]
    parents: Mapping[str, Tuple[str, ...]]
    children: Mapping[str, Tuple[str, ...]]
    resolved_keys: Mapping[str, Mapping[str, str]]
    raw_defined: FrozenSet[str]
    versions: Versions

    def mentions_for(self, section: str) -> Tuple[Mention, ...]:
        return self.mentions.get(section, ())

    def parents_of(self, section: str) -> Tuple[str, ...]:
        return self.parents.get(section, ())

    def children_of(self, section: str) -> Tuple[str, ...]:
        return self.children.get(section, ())

    def keys_of(self, section: str) -> Mapping[str, str]:
        return self.resolved_keys.get(section, EMPTY_KEYS)

    def contains(self, section: str) -> bool:
        return section in self.resolved_keys

    def was_raw_defined(self, section: str) -> bool:
        return section in self.raw_defined

    @property
    def mention_count(self) -> int:
        return sum(len(group) for group in self.mentions.values())


# --------------------------------------------------------------------------- #
# Построение
# --------------------------------------------------------------------------- #

def build_index(source: GraphInput) -> ResolvedMentionIndex:
    """Фаза A целиком. Детерминированный обход: порядок источника сохраняется."""
    resolved_keys: Dict[str, Mapping[str, str]] = {}
    parents: Dict[str, Tuple[str, ...]] = {}
    children: Dict[str, List[str]] = {}

    for section in source.iter_resolved_sections():
        name = normalize_name(section.name)
        resolved_keys[name] = dict(section.keys)
        normalized_parents = tuple(normalize_name(p) for p in section.parents)
        parents[name] = normalized_parents
        for parent in normalized_parents:
            children.setdefault(parent, []).append(name)

    known = frozenset(resolved_keys)
    collected: Dict[str, List[Mention]] = {}
    defs: Dict[str, List[Mention]] = {}

    def add(mention: Mention) -> None:
        collected.setdefault(mention.target_section, []).append(mention)

    def scan_text(text: str, target_file: str, user_section: Optional[str],
                  key: Optional[str], origin: MentionOrigin,
                  meta: Optional[SourceMeta]) -> None:
        for token in tokenize(text):
            hit = resolve_target(token, known)
            if hit is None:
                continue
            target, kind = hit
            label, strength = classify(origin, kind)
            add(Mention(target, target_file, user_section, key,
                        origin, label, strength, meta))

    # 1. Итоговое дерево: high/medium из токенов + DEFINITION для файлов-владельцев.
    for tree_file in source.iter_tree_files():
        for section in tree_file.sections:
            section_name = normalize_name(section.name)
            first_meta = None
            for line in section.lines:
                meta = SourceMeta(owner=line.owner, disk_path=line.disk_path,
                                  chain=tuple(line.chain))
                if first_meta is None:
                    first_meta = meta
                scan_text(line.key, tree_file.name, section_name, line.key,
                          MentionOrigin.KEY, meta)
                scan_text(line.value, tree_file.name, section_name, line.key,
                          MentionOrigin.VALUE, meta)

            if section_name in known and first_meta is not None:
                label, strength = classify(MentionOrigin.DEFINITION, MatchKind.EXACT)
                defs.setdefault(section_name, []).append(Mention(
                    section_name, tree_file.name, section_name, None,
                    MentionOrigin.DEFINITION, label, strength, first_meta,
                ))

    # 2. Сырые комментарии: движок их не видит -> low.
    for comment in source.iter_raw_comments():
        scan_text(comment.text, comment.file, comment.section, None,
                  MentionOrigin.COMMENT, None)

    # 3. Имена файлов: low.
    for file_name in source.iter_tree_file_names():
        scan_text(file_name, file_name, None, None, MentionOrigin.FILENAME, None)

    # 4. XML и скрипты: итогового дерева нет -> сырой скан.
    for xml_file in source.iter_xml_files():
        for fragment in xml_file.fragments:
            scan_text(fragment, xml_file.name, None, None, MentionOrigin.XML, None)
    for script_file in source.iter_script_files():
        for line in script_file.lines:
            scan_text(line, script_file.name, None, None, MentionOrigin.SCRIPT, None)

    # Определения секций вливаются в общий сбор упоминаний.
    for target, group in defs.items():
        collected.setdefault(target, []).extend(group)

    return ResolvedMentionIndex(
        mentions={target: tuple(group) for target, group in collected.items()},
        parents=parents,
        children={name: tuple(group) for name, group in children.items()},
        resolved_keys=resolved_keys,
        raw_defined=frozenset(source.raw_defined_sections()),
        versions=source.versions(),
    )