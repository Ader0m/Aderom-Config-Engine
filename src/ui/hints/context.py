# -*- coding: utf-8 -*-
"""Сборка :class:`sdk.ui.hints.HintContext` из адаптера и цели."""

from __future__ import annotations

from PyQt5.QtGui import QColor

from app.hints import build_ltx_line_info
from sdk.ui.hints import (
    HintContext,
    HintTargetInfo,
    LANGUAGE_LTX,
)

_WINDOW_BACK = 200
_WINDOW_AROUND = 50
_MAX_LOCAL_KEYS = 300


def _rgba(value):
    if value is None:
        return None
    if callable(value):
        try:
            value = value()
        except Exception:
            return None
    if isinstance(value, (tuple, list)) and len(value) >= 3:
        return (
            int(value[0]) & 0xFF,
            int(value[1]) & 0xFF,
            int(value[2]) & 0xFF,
            int(value[3]) & 0xFF if len(value) > 3 else 255,
        )
    if isinstance(value, QColor):
        return (
            value.red() & 0xFF,
            value.green() & 0xFF,
            value.blue() & 0xFF,
            value.alpha() & 0xFF,
        )
    if isinstance(value, str):
        color = QColor(value)
        if color.isValid():
            return (
                color.red() & 0xFF,
                color.green() & 0xFF,
                color.blue() & 0xFF,
                color.alpha() & 0xFF,
            )
    return None


def resolve_target(target) -> HintTargetInfo:
    if callable(target):
        try:
            target = target()
        except Exception:
            target = None
    if target is None:
        return HintTargetInfo()
    if isinstance(target, HintTargetInfo):
        return target
    if isinstance(target, dict):
        return HintTargetInfo(**target)
    return HintTargetInfo(
        surface=getattr(target, "surface", "plain"),
        language=getattr(target, "language", "plain"),
        path=getattr(target, "path", None),
        tab_id=getattr(target, "tab_id", ""),
        role=getattr(target, "role", ""),
        default_color=getattr(target, "default_color", None),
        read_only=getattr(target, "read_only", None),
    )


def _is_ltx(target):
    return str(target.language or "").lower() == LANGUAGE_LTX


def build_context(
    adapter,
    target,
    with_selection=False,
) -> HintContext:
    """Собрать снимок контекста для провайдеров.

    ``with_selection`` по умолчанию выключен: копирование выделения на
    каждом движении курсора дорого, а в QScintilla вызов
    ``selectedText()`` во время перетаскивания/Shift-клика приводил к
    повреждению памяти (известный баг QScintilla). Ни один провайдер
    сейчас выделение не использует, поэтому читаем его только по
    явному запросу.
    """
    target = resolve_target(target)
    line = int(adapter.cursor_line())
    column = int(adapter.cursor_column())
    line_text = adapter.line_text(line)
    column = max(0, min(column, len(line_text)))
    prefix = line_text[:column]
    suffix = line_text[column:]

    in_comment = False
    in_string = False
    section = ""
    section_kind = ""
    key = ""
    value_kind = ""
    local_keys = ()
    info = None
    if _is_ltx(target):
        info = build_ltx_line_info(adapter.line_text, line, column)
        in_comment = info.in_comment
        in_string = info.in_string
        section = info.section
        section_kind = info.section_kind
        key = info.key
        value_kind = info.value_kind
        local_keys = info.local_keys

    local_sections = ()
    provider = getattr(adapter, "local_sections", None)
    if callable(provider):
        try:
            local_sections = tuple(provider() or ())
        except Exception:
            local_sections = ()
    if _is_ltx(target) and not local_sections and info is not None:
        local_sections = info.local_sections

    editable = bool(adapter.is_editable())
    if target.read_only:
        editable = False

    default_color = _rgba(target.default_color)
    if default_color is None:
        default_color = _rgba(adapter.default_text_color())

    return HintContext(
        target=target,
        adapter_kind=str(getattr(adapter, "kind", "") or ""),
        line=line,
        column=column,
        line_text=line_text,
        prefix=prefix,
        suffix=suffix,
        selected_text=(
            str(adapter.selected_text() or "") if with_selection else ""
        ),
        default_color=default_color,
        editable=editable,
        in_comment=in_comment,
        in_string=in_string,
        section=section,
        section_kind=section_kind,
        key=key,
        value_kind=value_kind,
        document_revision=int(adapter.document_revision() or 0),
        local_sections=local_sections,
        local_keys=local_keys,
    )
