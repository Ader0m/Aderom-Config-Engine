# -*- coding: utf-8 -*-
"""Подсказки ключей локализации в значениях ``inv_name``/``description``."""

from __future__ import annotations

from sdk.engine_text import engine_text_lines
from sdk.hints import (
    KIND_LOCALIZATION,
    LANGUAGE_LTX,
    SURFACE_RAW,
    SURFACE_RESOLVED,
    HintItem,
    HintResult,
)
from ui.hints.triggers import localization_trigger

_SURFACES = frozenset({SURFACE_RAW, SURFACE_RESOLVED})


def _preview(text, limit=90):
    try:
        lines = engine_text_lines(text)
    except Exception:
        lines = (str(text or ""),)
    value = " ".join(part.strip() for part in lines if part.strip())
    if len(value) > limit:
        value = value[: limit - 1] + "…"
    return value


class LocalizationKeyProvider:
    id = "ui.localization_key"
    priority = 90
    surfaces = _SURFACES
    languages = frozenset({LANGUAGE_LTX})
    allow_in_comment = False

    def __init__(self, catalogs):
        self._catalogs = catalogs

    def on_catalogs_changed(self):
        return None

    def matches(self, context):
        trigger = localization_trigger(context.prefix, context.suffix)
        if trigger is None:
            return None

        catalog = self._catalogs.localization
        if catalog is None:
            return HintResult(
                provider_id=self.id,
                items=(),
                handled=True,
                kind=KIND_LOCALIZATION,
                total=0,
            )

        keys = catalog.prefix(trigger.prefix, limit=200)
        items = []
        for key in keys:
            entry = catalog.entry(key)
            detail = ""
            if entry is not None and getattr(entry, "path", None) is not None:
                detail = "%s:%d" % (
                    entry.path.name,
                    int(getattr(entry, "line", 0) or 0),
                )
            items.append(
                HintItem(
                    label=key,
                    insert_text=key,
                    description=_preview(catalog.text(key)),
                    detail=detail,
                    kind=KIND_LOCALIZATION,
                    sort_key=key.lower(),
                )
            )

        return HintResult(
            provider_id=self.id,
            items=tuple(items),
            handled=True,
            replace_before=len(trigger.prefix),
            replace_after=trigger.replace_after,
            prefix=trigger.prefix,
            kind=KIND_LOCALIZATION,
            total=len(items),
        )
