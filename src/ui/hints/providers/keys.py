# -*- coding: utf-8 -*-
"""Подсказки ключей внутри секции LTX/DLTX."""

from __future__ import annotations

from sdk.hints import (
    KIND_KEY,
    LANGUAGE_LTX,
    SURFACE_RAW,
    SURFACE_RESOLVED,
    HintItem,
    HintResult,
)
from ui.hints.triggers import key_trigger

_SURFACES = frozenset({SURFACE_RAW, SURFACE_RESOLVED})
_MAX_DEPTH = 5


def _parent_keys(catalog, section, depth=0, seen=None):
    seen = set() if seen is None else seen
    name = str(section or "").strip().lower()
    if not name or name in seen or depth > _MAX_DEPTH:
        return []
    seen.add(name)
    info = catalog.find(name) if catalog is not None else None
    if info is None:
        return []
    result = [(key, name) for key in info.keys]
    for parent in info.parents:
        result.extend(_parent_keys(catalog, parent, depth + 1, seen))
    return result


class KeyProvider:
    id = "ui.ltx_key"
    priority = 70
    surfaces = _SURFACES
    languages = frozenset({LANGUAGE_LTX})
    allow_in_comment = False

    def __init__(self, catalogs):
        self._catalogs = catalogs

    def on_catalogs_changed(self):
        return None

    def matches(self, context):
        trigger = key_trigger(context.prefix)
        if trigger is None:
            return None
        if not context.section:
            return HintResult(
                provider_id=self.id,
                items=(),
                handled=True,
                kind=KIND_KEY,
                total=0,
            )

        prefix = trigger.prefix.lower()
        items = []
        seen = set()

        for key, value in context.local_keys:
            low = key.lower()
            if prefix and not low.startswith(prefix):
                continue
            if low in seen:
                continue
            seen.add(low)
            description = ""
            if value:
                description = "= " + value[:60]
            items.append(
                HintItem(
                    label=key,
                    insert_text=key,
                    description=description,
                    kind=KIND_KEY,
                    sort_key=low,
                )
            )

        catalog = self._catalogs.sections
        for key, owner in _parent_keys(catalog, context.section):
            low = key.lower()
            if prefix and not low.startswith(prefix):
                continue
            if low in seen:
                continue
            seen.add(low)
            items.append(
                HintItem(
                    label=key,
                    insert_text=key,
                    description="из секции %s" % owner,
                    kind=KIND_KEY,
                    sort_key=low,
                )
            )
            if len(items) >= 200:
                break

        return HintResult(
            provider_id=self.id,
            items=tuple(items),
            handled=True,
            replace_before=len(trigger.prefix),
            prefix=trigger.prefix,
            kind=KIND_KEY,
            total=len(items),
        )
