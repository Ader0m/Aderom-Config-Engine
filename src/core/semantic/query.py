# -*- coding: utf-8 -*-
"""Запросы к семантической модели корня.

Сервис не строит модель сам: он просит ``ResolverService.assemble_semantic``
(результат кэшируется по снимку) и отвечает на запросы секций/свойств.
Тяжёлый резолв должен вызываться вне UI-потока через task API; здесь нет
Qt и нет записи на диск.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from core.semantic.models import (
    Diagnostic,
    Diagnostics,
    SectionFindPage,
    SectionPage,
    SectionSnapshot,
)
from core.tools.paths import path_key


def missing_include_diagnostics(forest, root_path) -> Diagnostics:
    """Диагностика include-директив, не разрешившихся в поддереве корня."""
    root_key = path_key(root_path)
    node_parent = getattr(forest, "node_parent", {}) or {}
    normalized_parents = {
        path_key(path): parent for path, parent in node_parent.items()
    }
    items = []
    for parent, include in getattr(forest, "missing_includes", ()) or ():
        current = Path(parent)
        seen = set()
        belongs = False
        while current is not None:
            current_key = path_key(current)
            if current_key in seen:
                break
            if current_key == root_key:
                belongs = True
                break
            seen.add(current_key)
            parent_value = node_parent.get(current)
            if parent_value is None:
                parent_value = normalized_parents.get(current_key)
            current = parent_value
        if not belongs:
            continue
        items.append(
            Diagnostic(
                code="missing_include",
                message="Файл %s: не найден include %s"
                % (parent, include),
                severity="warning",
            )
        )
    return Diagnostics(tuple(items))


class SectionQueryService:
    """Секции, свойства, цепочки и диагностический дамп одного корня."""

    def __init__(self, resolver_service):
        self._resolver = resolver_service

    def root(self, root_path, forest, config_dir, overlay=None, log=None):
        snapshot_id = str(getattr(forest, "cache_id", "") or "")
        root = self._resolver.assemble_semantic(
            root_path,
            forest,
            config_dir,
            log=log,
            overlay=overlay,
            snapshot_id=snapshot_id,
        )
        extra = missing_include_diagnostics(forest, root_path)
        if extra:
            root = replace(
                root,
                diagnostics=Diagnostics(
                    tuple(root.diagnostics.items) + tuple(extra.items)
                ),
            )
        return root

    def section(self, root_path, name, forest, config_dir, overlay=None,
                log=None) -> SectionSnapshot | None:
        return self._index(
            root_path,
            forest,
            config_dir,
            overlay=overlay,
            log=log,
        ).get(name)

    def find_sections(self, root_path, query, forest, config_dir,
                      overlay=None, log=None, exact=False, offset=0,
                      limit=100) -> SectionFindPage:
        return self._index(
            root_path,
            forest,
            config_dir,
            overlay=overlay,
            log=log,
        ).find(
            query,
            exact=exact,
            offset=offset,
            limit=limit,
        )

    def properties(self, root_path, name, forest, config_dir, overlay=None,
                   log=None, keys=None, offset=0,
                   limit=100) -> SectionPage:
        root = self.root(
            root_path,
            forest,
            config_dir,
            overlay=overlay,
            log=log,
        )
        page = root.sections.properties(
            name,
            keys=keys,
            offset=offset,
            limit=limit,
        )
        if page.section is None:
            page = replace(
                page,
                diagnostics=self._unknown(root.diagnostics, name),
            )
        else:
            page = replace(page, diagnostics=root.diagnostics)
        return page

    def dump(self, root_path, forest, config_dir, overlay=None, log=None,
             section=None) -> str:
        index = self._index(
            root_path,
            forest,
            config_dir,
            overlay=overlay,
            log=log,
        )
        if section is not None and index.get(section) is None:
            raise KeyError("Секция '%s' не найдена" % section)
        return index.dump(section=section)

    def iter_dump(self, root_path, forest, config_dir, overlay=None,
                  log=None, section=None):
        index = self._index(
            root_path,
            forest,
            config_dir,
            overlay=overlay,
            log=log,
        )
        if section is not None and index.get(section) is None:
            raise KeyError("Секция '%s' не найдена" % section)
        return index.iter_dump(section=section)

    # -------------------------------------------------- внутреннее
    def _index(self, root_path, forest, config_dir, overlay=None, log=None):
        return self.root(
            root_path,
            forest,
            config_dir,
            overlay=overlay,
            log=log,
        ).sections

    @staticmethod
    def _unknown(diagnostics, name) -> Diagnostics:
        return Diagnostics(
            tuple(diagnostics.items)
            + (
                Diagnostic(
                    code="unknown_section",
                    message="Секция '%s' не найдена" % name,
                    severity="info",
                    section=str(name),
                ),
            )
        )


__all__ = ["SectionQueryService"]
