# -*- coding: utf-8 -*-
"""Контракты движка подсказок (SDK).

Модуль намеренно не импортирует Qt, ``core``, ``ui`` или ``modules``.
Провайдеры и адаптеры работают с обычными данными; UI-слой сам рисует
popup и обрабатывает клавиши.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Protocol, Tuple

# ---------------------------------------------------------------- поверхности
SURFACE_RAW = "raw"
SURFACE_RESOLVED = "resolved"
SURFACE_ENGINE_TEXT = "engine_text"
SURFACE_PLAIN = "plain"

LANGUAGE_LTX = "ltx"
LANGUAGE_XML = "xml"
LANGUAGE_LOCALIZATION_TEXT = "localization_text"
LANGUAGE_PLAIN = "plain"

MATCH_PREFIX = "prefix"
MATCH_CONTAINS = "contains"
MATCH_FUZZY = "fuzzy"

KIND_COLOR = "color"
KIND_SECTION = "section"
KIND_KEY = "key"
KIND_INCLUDE = "include"
KIND_LOCALIZATION = "localization"
KIND_DIRECTIVE = "directive"
KIND_TEXT = "text"


@dataclass(frozen=True)
class HintTargetInfo:
    """Описание поверхности, к которой подключён движок подсказок.

    ``default_color`` может быть callable, чтобы палитра читалась в
    момент показа. Контроллер разрешает значение в ``HintContext``.
    """

    surface: str = SURFACE_PLAIN
    language: str = LANGUAGE_PLAIN
    path: Optional[Path] = None
    tab_id: str = ""
    role: str = ""
    default_color: object = None
    read_only: Optional[bool] = None


@dataclass(frozen=True)
class HintContext:
    """Снимок позиции, который получает провайдер."""

    target: HintTargetInfo
    adapter_kind: str = ""
    line: int = 0
    column: int = 0
    line_text: str = ""
    prefix: str = ""
    suffix: str = ""
    selected_text: str = ""
    default_color: Optional[Tuple[int, int, int, int]] = None
    editable: bool = True
    in_comment: bool = False
    in_string: bool = False
    section: str = ""
    section_kind: str = ""
    key: str = ""
    value_kind: str = ""
    document_revision: int = 0
    local_sections: Tuple[str, ...] = ()
    local_keys: Tuple[Tuple[str, str], ...] = ()

    @property
    def surface(self):
        return self.target.surface

    @property
    def language(self):
        return self.target.language

    @property
    def path(self):
        return self.target.path


@dataclass(frozen=True)
class HintItem:
    """Единый элемент списка подсказок."""

    label: str
    insert_text: str
    description: str = ""
    detail: str = ""
    kind: str = KIND_TEXT
    sort_key: str = ""
    swatch: Optional[Tuple[int, int, int, int]] = None
    keep_open: bool = False
    payload: object = None


@dataclass(frozen=True)
class HintResult:
    """Ответ провайдера: диапазон замены, элементы и поведение."""

    provider_id: str
    items: Tuple[HintItem, ...] = ()
    handled: bool = False
    replace_before: int = 0
    replace_after: int = 0
    prefix: str = ""
    suffix: str = ""
    caret_offset: int = 0
    close_after_insert: bool = True
    reopen_after_insert: bool = False
    kind: str = KIND_TEXT
    total: int = 0

    def with_items(self, items: Iterable[HintItem], total=None):
        values = tuple(items)
        return HintResult(
            provider_id=self.provider_id,
            items=values,
            handled=self.handled,
            replace_before=self.replace_before,
            replace_after=self.replace_after,
            prefix=self.prefix,
            suffix=self.suffix,
            caret_offset=self.caret_offset,
            close_after_insert=self.close_after_insert,
            reopen_after_insert=self.reopen_after_insert,
            kind=self.kind,
            total=len(values) if total is None else int(total),
        )


class HintProvider(Protocol):
    """Поставщик подсказок для одного контекста/типа данных."""

    id: str
    priority: int
    surfaces: frozenset
    languages: frozenset
    allow_in_comment: bool

    def matches(self, context: HintContext) -> Optional[HintResult]:
        ...

    def on_catalogs_changed(self):
        ...


class HintsHandle(Protocol):
    """Ручка управления подсказками конкретного текстового поля."""

    enabled: bool

    def set_enabled(self, enabled: bool) -> None:
        ...

    def set_target(self, target) -> None:
        ...

    def refresh(self) -> None:
        ...

    def close(self) -> None:
        ...

    def detach(self) -> None:
        ...

    def is_active(self) -> bool:
        ...


class NullHintsHandle:
    """Безопасная заглушка, если движок подсказок не подключён."""

    enabled = False

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)

    def set_target(self, target) -> None:
        return None

    def refresh(self) -> None:
        return None

    def close(self) -> None:
        return None

    def detach(self) -> None:
        return None

    def is_active(self) -> bool:
        return False


@dataclass(frozen=True)
class HintRegistration:
    """Описание регистрации провайдера (для будущего SDK-метода)."""

    provider: HintProvider
    order: int = 0
    active: bool = True
