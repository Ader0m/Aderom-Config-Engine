# -*- coding: utf-8 -*-
"""Подсказки секций и родителей LTX/DLTX."""

from __future__ import annotations

from sdk.hints import (
    KIND_SECTION,
    LANGUAGE_LTX,
    SURFACE_RAW,
    SURFACE_RESOLVED,
    HintItem,
    HintResult,
)
from ui.hints.triggers import parent_trigger, section_trigger

_SURFACES = frozenset({SURFACE_RAW, SURFACE_RESOLVED})


def _description(info):
    if info is None:
        return "в этом файле"
    if info.is_deleted:
        return "удалена в DLTX"
    if info.path is not None:
        return "%s:%d" % (info.path.name, info.line or 0)
    return ""


def _token_after(context, markers=(",", ";", "//")):
    end = len(context.suffix)
    for marker in markers:
        index = context.suffix.find(marker)
        if 0 <= index < end:
            end = index
    return end


class SectionProvider:
    id = "ui.ltx_section"
    priority = 80
    surfaces = _SURFACES
    languages = frozenset({LANGUAGE_LTX})
    allow_in_comment = False

    def __init__(self, catalogs):
        self._catalogs = catalogs

    def on_catalogs_changed(self):
        return None

    def matches(self, context):
        trigger = section_trigger(context.prefix)
        if trigger is None:
            return None

        prefix = trigger.prefix.lower()
        catalog = self._catalogs.sections
        local = set(context.local_sections)
        items = []
        seen = set()

        for name in context.local_sections:
            if prefix and not name.startswith(prefix):
                continue
            if name in seen:
                continue
            seen.add(name)
            items.append(
                HintItem(
                    label=name,
                    insert_text=name,
                    description="в этом файле",
                    kind=KIND_SECTION,
                    sort_key=name,
                )
            )

        if catalog is not None:
            for info in catalog.prefix(prefix, limit=200):
                name = info.name
                if name in seen:
                    continue
                seen.add(name)
                items.append(
                    HintItem(
                        label=name,
                        insert_text=name,
                        description=_description(info),
                        kind=KIND_SECTION,
                        sort_key=name,
                    )
                )
                if len(items) >= 200:
                    break

        items.sort(
            key=lambda item: (
                1 if item.description == "удалена в DLTX" else 0,
                0 if item.description == "в этом файле" else 1,
                item.sort_key,
            )
        )
        closing = context.suffix.find("]")
        replace_after = closing + 1 if closing >= 0 else 0
        return HintResult(
            provider_id=self.id,
            items=tuple(items),
            handled=True,
            replace_before=len(trigger.prefix),
            replace_after=replace_after,
            prefix=trigger.prefix,
            suffix="]",
            kind=KIND_SECTION,
            total=len(items),
        )


class ParentProvider:
    id = "ui.ltx_parent"
    priority = 75
    surfaces = _SURFACES
    languages = frozenset({LANGUAGE_LTX})
    allow_in_comment = False

    def __init__(self, catalogs):
        self._catalogs = catalogs

    def on_catalogs_changed(self):
        return None

    def matches(self, context):
        trigger = parent_trigger(context.prefix)
        if trigger is None:
            return None

        prefix = trigger.prefix.lower()
        excluded = set(trigger.exclude)
        if context.section:
            excluded.add(str(context.section).lower())
        catalog = self._catalogs.sections
        items = []
        seen = set()

        for name in context.local_sections:
            if name in excluded or name in seen:
                continue
            if prefix and not name.startswith(prefix):
                continue
            seen.add(name)
            items.append(
                HintItem(
                    label=name,
                    insert_text=name,
                    description="в этом файле",
                    kind=KIND_SECTION,
                    sort_key=name,
                )
            )

        if catalog is not None:
            for info in catalog.prefix(prefix, limit=200):
                name = info.name
                if name in excluded or name in seen:
                    continue
                seen.add(name)
                items.append(
                    HintItem(
                        label=name,
                        insert_text=name,
                        description=_description(info),
                        kind=KIND_SECTION,
                        sort_key=name,
                    )
                )
                if len(items) >= 200:
                    break

        return HintResult(
            provider_id=self.id,
            items=tuple(items),
            handled=True,
            replace_before=len(trigger.prefix),
            replace_after=_token_after(context),
            prefix=trigger.prefix,
            kind=KIND_SECTION,
            total=len(items),
        )
