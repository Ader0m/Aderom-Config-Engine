# -*- coding: utf-8 -*-
"""Подсказки путей после ``#include`` (только сырой LTX)."""

from __future__ import annotations

from sdk.hints import (
    KIND_DIRECTIVE,
    KIND_INCLUDE,
    LANGUAGE_LTX,
    SURFACE_RAW,
    HintItem,
    HintResult,
)
from ui.hints.triggers import directive_trigger, include_trigger


class IncludePathProvider:
    id = "ui.include_path"
    priority = 100
    surfaces = frozenset({SURFACE_RAW})
    languages = frozenset({LANGUAGE_LTX})
    allow_in_comment = False

    def __init__(self, catalogs):
        self._catalogs = catalogs

    def on_catalogs_changed(self):
        return None

    def matches(self, context):
        trigger = include_trigger(context.prefix)
        if trigger is None:
            return None

        catalog = self._catalogs.includes
        entries = ()
        if catalog is not None:
            entries = catalog.prefix(
                trigger.prefix,
                limit=200,
                suffix=".ltx",
            )

        items = []
        current_dir = (
            trigger.prefix.lower()
            if trigger.prefix.endswith("/")
            else ""
        )
        for entry in entries:
            if current_dir and entry.rel_path.lower() == current_dir:
                continue
            is_dir = entry.is_dir
            items.append(
                HintItem(
                    label=entry.rel_path,
                    insert_text=entry.rel_path,
                    description="каталог" if is_dir else "файл",
                    kind=KIND_INCLUDE,
                    sort_key=entry.rel_path.lower(),
                    keep_open=is_dir,
                )
            )

        if trigger.quoted:
            close = context.suffix.find('"')
            replace_after = close if close >= 0 else 0
        else:
            end = len(context.suffix)
            for marker in (" ", "\t", ";", "//", "\r", "\n"):
                index = context.suffix.find(marker)
                if 0 <= index < end:
                    end = index
            replace_after = end

        return HintResult(
            provider_id=self.id,
            items=tuple(items),
            handled=True,
            replace_before=len(trigger.prefix),
            replace_after=replace_after,
            prefix=trigger.prefix,
            kind=KIND_INCLUDE,
            total=len(items),
        )


class DirectiveProvider:
    id = "ui.ltx_directive"
    priority = 60
    surfaces = frozenset({SURFACE_RAW})
    languages = frozenset({LANGUAGE_LTX})
    allow_in_comment = False

    def __init__(self, catalogs=None):
        self._catalogs = catalogs

    def on_catalogs_changed(self):
        return None

    def matches(self, context):
        trigger = directive_trigger(context.prefix)
        if trigger is None:
            return None
        items = (
            HintItem(
                label="include",
                insert_text="include",
                description="подключить LTX-файл",
                kind=KIND_DIRECTIVE,
                sort_key="include",
            ),
        )
        return HintResult(
            provider_id=self.id,
            items=items,
            handled=True,
            replace_before=len(trigger.prefix),
            suffix=" ",
            prefix=trigger.prefix,
            kind=KIND_DIRECTIVE,
            reopen_after_insert=True,
            total=len(items),
        )
