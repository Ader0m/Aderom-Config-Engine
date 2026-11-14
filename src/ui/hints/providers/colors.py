# -*- coding: utf-8 -*-
"""Подсказка ``%c[name]`` для текста движка."""

from __future__ import annotations

from sdk.hints import (
    KIND_COLOR,
    LANGUAGE_LOCALIZATION_TEXT,
    SURFACE_ENGINE_TEXT,
    HintItem,
    HintResult,
)
from ui.hints.triggers import color_trigger


def _hex(rgba):
    r, g, b, a = rgba
    if a >= 255:
        return "#%02X%02X%02X" % (r, g, b)
    return "#%02X%02X%02X%02X" % (a, r, g, b)


class ColorTagProvider:
    id = "ui.color_tags"
    priority = 90
    surfaces = frozenset({SURFACE_ENGINE_TEXT})
    languages = frozenset({LANGUAGE_LOCALIZATION_TEXT})
    allow_in_comment = False

    def __init__(self, catalogs):
        self._catalogs = catalogs

    def on_catalogs_changed(self):
        return None

    def matches(self, context):
        trigger = color_trigger(context.prefix)
        if trigger is None:
            return None

        catalog = self._catalogs.colors
        prefix = trigger.prefix
        low_prefix = prefix.lower()
        items = []
        total = 0

        if low_prefix == "" or low_prefix in "default":
            swatch = context.default_color
            items.append(
                HintItem(
                    label="default",
                    insert_text="default",
                    description="цвет текста контрола",
                    detail="",
                    kind=KIND_COLOR,
                    sort_key="0000",
                    swatch=swatch,
                )
            )
            total += 1

        if catalog is not None:
            ranked = []
            for entry in catalog.entries:
                name = entry.name
                low = name.lower()
                if low_prefix and low_prefix not in low:
                    continue
                position = low.find(low_prefix) if low_prefix else 0
                ranked.append(
                    (
                        0 if low.startswith(low_prefix) else 1,
                        position,
                        low,
                        entry,
                    )
                )
            ranked.sort(key=lambda value: value[:3])
            for _start, _position, _low, entry in ranked:
                swatch = entry.rgba
                items.append(
                    HintItem(
                        label=entry.name,
                        insert_text=entry.name,
                        description=_hex(swatch),
                        kind=KIND_COLOR,
                        sort_key=entry.name.lower(),
                        swatch=swatch,
                    )
                )
                total += 1
                if len(items) >= 200:
                    break

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
            caret_offset=0,
            kind=KIND_COLOR,
            total=total,
        )
