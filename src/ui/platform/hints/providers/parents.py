# -*- coding: utf-8 -*-
"""Подсказки имён родителей для карточки предмета."""

from __future__ import annotations

from sdk.ui.hints import (
    KIND_SECTION,
    LANGUAGE_PLAIN,
    SURFACE_PLAIN,
    HintItem,
    HintResult,
)


def _description(info):
    if info is None:
        return ""
    if info.is_deleted:
        return "удалена в DLTX"
    if info.path is not None:
        return "%s:%d" % (info.path.name, info.line or 0)
    return ""


class ParentNameProvider:
    """Список секций для поля «Родитель» вне LTX-строки."""

    id = "ui.item_parent"
    priority = 70
    surfaces = frozenset({SURFACE_PLAIN})
    languages = frozenset({LANGUAGE_PLAIN})
    allow_in_comment = False
    enabled = True

    def __init__(self, catalogs):
        self._catalogs = catalogs

    def on_catalogs_changed(self):
        return None

    def matches(self, context):
        if str(context.target.role or "") != "item.parent":
            return None
        prefix = str(context.prefix or "")
        items = []
        catalog = self._catalogs.sections
        if catalog is not None:
            for info in catalog.prefix(prefix, limit=200):
                items.append(
                    HintItem(
                        label=info.name,
                        insert_text=info.name,
                        description=_description(info),
                        kind=KIND_SECTION,
                        sort_key=info.name,
                    )
                )
        return HintResult(
            provider_id=self.id,
            items=tuple(items),
            handled=True,
            replace_before=len(prefix),
            replace_after=0,
            prefix=prefix,
            kind=KIND_SECTION,
            total=len(items),
        )
