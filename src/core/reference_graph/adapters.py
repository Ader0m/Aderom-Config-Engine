# -*- coding: utf-8 -*-
"""Адаптеры портов GraphInput к текущим сервисам ядра.

ForestStructureAdapter — имена файлов из LtxForest (без чтения содержимого);
ResolverTreeAdapter    — объединение собранных деревьев Resolver4 по ВСЕМ корням;
ParsedRawAdapter       — заглушка v2: сырьё не читается вовсе.
"""
from __future__ import annotations

import hashlib
import os

from .graph_input import (Line, ResolvedSection, TreeFile, TreeSection,
                          assemble_graph_input)
from .models import ChainLink


def _snapshot_id(forest) -> str:
    blob = "\n".join(str(f) for f in sorted(forest.files, key=lambda p: str(p).lower()))
    return hashlib.md5(blob.encode("utf-8", "replace")).hexdigest()


class ForestStructureAdapter:
    def __init__(self, forest, vfs_snapshot_id=None):
        self._forest = forest
        self._vfs_snapshot = vfs_snapshot_id or _snapshot_id(forest)

    def vfs_snapshot(self):
        return self._vfs_snapshot

    def ltx_file_names(self):
        return tuple(self._forest.rel_paths.get(f, str(f)) for f in self._forest.files)

    def xml_file_names(self):
        return ()

    def script_file_names(self):
        return ()


class ResolverTreeAdapter:
    """Объединение собранных деревьев: по одному LtxResolver на корень.

    Один и тот же файл может войти в несколько корней (общие include);
    строки дедуплицируются по (файл, секция, ключ), секции — по имени
    (первый корень выигрывает; порядок forest.roots детерминирован,
    system_root в нём первый).
    """

    def __init__(self, resolvers, vfs_snapshot_id, resolver_version="4.0", forest=None):
        self._resolvers = tuple(resolvers)
        self._vfs_snapshot = vfs_snapshot_id
        self._resolver_version = resolver_version
        self._stem_to_rel = {}
        if forest is not None:
            for f in forest.files:
                stem = os.path.splitext(f.name)[0].lower()
                if stem not in self._stem_to_rel:
                    self._stem_to_rel[stem] = forest.rel_paths.get(f, str(f))

    def vfs_snapshot(self):
        return self._vfs_snapshot

    def resolver_version(self):
        return self._resolver_version

    @staticmethod
    def _value_str(value):
        if value is None:
            return ""
        if hasattr(value, "name"):
            return "<%s>" % value.name
        return str(value)

    def iter_tree_files(self):
        files = {}
        seen = set()
        for resolver in self._resolvers:
            for section_name, sect in resolver.data:
                for item in sect.data:
                    filename = getattr(item, "filename", "") or ""
                    if not filename:
                        continue
                    dedupe = (filename, section_name, item.first)
                    if dedupe in seen:
                        continue
                    seen.add(dedupe)
                    file_key = self._stem_to_rel.get(filename, filename)
                    lines = files.setdefault(file_key, {}).setdefault(section_name, [])
                    value_str = self._value_str(item.second)
                    lines.append(Line(
                        key=item.first or "",
                        value=value_str,
                        owner=filename,
                        disk_path=None,
                        chain=(ChainLink(file=filename, depth=getattr(item, "depth", 0),
                                         value=value_str, operator="",
                                         insert_index=getattr(item, "insertion_index", 0)),),
                    ))
        for file_key, sections in files.items():
            yield TreeFile(name=file_key, sections=tuple(
                TreeSection(name=sname, lines=tuple(lines))
                for sname, lines in sections.items()))

    def iter_resolved_sections(self):
        seen = set()
        for resolver in self._resolvers:
            for section_name, sect in resolver.data:
                if section_name in seen:
                    continue
                seen.add(section_name)
                keys = {}
                for item in sect.data:
                    if item.first:
                        keys[item.first] = self._value_str(item.second)
                yield ResolvedSection(name=section_name, parents=(), keys=keys)


class ParsedRawAdapter:
    """Заглушка v2: сырьё не читается. Комментарии/xml/скрипты вернутся позже."""

    def __init__(self, vfs_snapshot_id, parser_version="1.0"):
        self._vfs_snapshot = vfs_snapshot_id
        self._parser_version = parser_version

    def vfs_snapshot(self):
        return self._vfs_snapshot

    def parser_version(self):
        return self._parser_version

    def iter_raw_comments(self):
        return ()

    def iter_raw_ltx_files(self):
        return ()

    def xml_fragments(self, file_name):
        return ()

    def script_lines(self, file_name):
        return ()

    def raw_defined_section_names(self):
        return frozenset()


def create_graph_input(forest, resolvers, vfs_snapshot_id=None,
                       resolver_version="4.0", parser_version="1.0"):
    sid = vfs_snapshot_id or _snapshot_id(forest)
    return assemble_graph_input(
        ForestStructureAdapter(forest, sid),
        ResolverTreeAdapter(resolvers, sid, resolver_version, forest=forest),
        ParsedRawAdapter(sid, parser_version),
    )