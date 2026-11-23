# -*- coding: utf-8 -*-
"""Неизменяемые модели семантического резолва LTX.

Модели описывают уже разрешённое состояние корня: секции, свойства,
победителя и цепочку источников. Они не являются форматом UI и не
содержат Qt-зависимостей.

``SectionSnapshot`` из этого модуля — модель **разрешённой** секции.
Одноимённый класс в :mod:`core.patching.ltx.diff` — снимок секции для
создания патча; это разные понятия. Чтобы не смешивать публичные имена,
модели доступны как ``core.semantic.*`` и не добавляются в ``core.__all__``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from core.semantic.provenance import SectionIndex


SEMANTIC_MODEL_VERSION = 1


@dataclass(frozen=True, slots=True)
class RootRef:
    """Корневой LTX-файл, к которому относится модель."""

    path: Path
    snapshot_id: str = ""


@dataclass(frozen=True, slots=True)
class ModRef:
    """Мод MO2, из которого пришёл файл, если он известен."""

    name: str


@dataclass(frozen=True, slots=True)
class SourceRef:
    """Ссылка на объявление в исходном файле."""

    file: Path
    section: str
    line: int = 0
    mod: Optional[ModRef] = None


@dataclass(frozen=True, slots=True)
class ValueSource:
    """Происхождение одного значения.

    ``line`` пока равен ``0``: строки появятся при ленивом дополнении
    синтаксическим разбором. Модель не выдумывает номер строки.
    ``priority`` — глубина загрузки движка (меньше — выше приоритет).
    """

    file: Path
    section: str
    key: str
    mod: Optional[ModRef]
    priority: Optional[int]
    root: RootRef
    depth: Optional[int]
    line: int = 0
    layer: str = "resolved"     # raw | resolved | patch
    operation: str = "set"      # set | append | remove | delete | create


@dataclass(frozen=True, slots=True)
class OverrideChain:
    """Последовательность источников от раннего к победителю."""

    sources: Tuple[ValueSource, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.sources)

    def __len__(self) -> int:
        return len(self.sources)

    def __iter__(self):
        return iter(self.sources)

    @property
    def winner(self) -> Optional[ValueSource]:
        """Последний источник, который не является удалением."""
        for source in reversed(self.sources):
            if source.operation != "delete":
                return source
        return self.sources[-1] if self.sources else None

    @property
    def operations(self) -> Tuple[str, ...]:
        return tuple(source.operation for source in self.sources)


@dataclass(frozen=True, slots=True)
class PropertySnapshot:
    """Итоговое свойство секции и его происхождение."""

    key: str
    operation: str
    value: Optional[str]
    winner: ValueSource
    chain: OverrideChain
    confidence: str = "exact"   # exact | probable | heuristic


@dataclass(frozen=True, slots=True)
class SectionSnapshot:
    """Разрешённая секция.

    ``declaration`` — файл/строка первого заголовка секции, ``source`` —
    файл заголовка-победителя (override, если он есть). Свойства
    отсортированы по ключу.
    """

    name: str
    declaration: SourceRef
    kind: str                   # base | override | create | delete
    parents: Tuple[str, ...]
    properties: Tuple[PropertySnapshot, ...]
    source: SourceRef


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """Одна диагностическая запись резолва или запроса."""

    code: str
    message: str
    severity: str = "warning"   # info | warning | error
    section: Optional[str] = None
    key: Optional[str] = None


@dataclass(frozen=True, slots=True)
class Diagnostics:
    """Неизменяемый набор диагностических записей."""

    items: Tuple[Diagnostic, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    def of_severity(self, severity: str) -> Tuple[Diagnostic, ...]:
        return tuple(
            item for item in self.items if item.severity == severity
        )

    @property
    def warnings(self) -> Tuple[Diagnostic, ...]:
        return self.of_severity("warning")

    @property
    def errors(self) -> Tuple[Diagnostic, ...]:
        return self.of_severity("error")


@dataclass(frozen=True, slots=True)
class EngineVersions:
    """Версии, при которых построена модель."""

    resolver: str = ""
    resolver_signature: str = ""
    model: int = SEMANTIC_MODEL_VERSION


@dataclass(frozen=True, slots=True)
class ResolvedRoot:
    """Семантическая модель одного корня и её текстовая проекция."""

    snapshot_id: str
    root: RootRef
    text: str
    line_sources: tuple
    sections: "SectionIndex"
    diagnostics: Diagnostics
    engine: EngineVersions


@dataclass(frozen=True, slots=True)
class SectionPage:
    """Страница свойств одной секции."""

    section: Optional[SectionSnapshot]
    properties: Tuple[PropertySnapshot, ...]
    total: int
    offset: int
    limit: int
    diagnostics: Diagnostics = Diagnostics()


@dataclass(frozen=True, slots=True)
class SectionFindPage:
    """Страница результатов поиска секций по имени."""

    names: Tuple[str, ...]
    total: int
    offset: int
    limit: int


class ValueSourceInterner:
    """Интернер неизменяемых ``ValueSource``.

    Один и тот же источник встречается в тысячах унаследованных свойств.
    Интернер возвращает общий объект, поэтому модель не создаёт копию
    источника на каждую секцию.
    """

    __slots__ = ("_values",)

    def __init__(self):
        self._values = {}

    def intern(self, source: ValueSource) -> ValueSource:
        key = (
            source.file,
            source.section,
            source.key,
            source.mod,
            source.priority,
            source.root,
            source.depth,
            source.line,
            source.layer,
            source.operation,
        )
        cached = self._values.get(key)
        if cached is not None:
            return cached
        self._values[key] = source
        return source

    def __len__(self) -> int:
        return len(self._values)


__all__ = [
    "SEMANTIC_MODEL_VERSION",
    "Diagnostic",
    "Diagnostics",
    "EngineVersions",
    "ModRef",
    "OverrideChain",
    "PropertySnapshot",
    "ResolvedRoot",
    "RootRef",
    "SectionFindPage",
    "SectionPage",
    "SectionSnapshot",
    "SourceRef",
    "ValueSource",
    "ValueSourceInterner",
]
