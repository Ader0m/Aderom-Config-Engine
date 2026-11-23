# -*- coding: utf-8 -*-
"""Происхождение значений из данных ``Resolver4``.

Инструментированный подкласс ``LtxResolver`` только **записывает** данные,
которые уже вычисляет движок: объявления до сортировки/фильтрации,
родителей, CSV-операции, удаления и предупреждения. Семантика сортировки,
слияния и вычисления значений не меняется.

Модель строится лениво: при первом запросе секции. Полный дамп — отдельная
диагностическая операция, а не обычный путь открытия файла.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

from core.models import LineSource
from core.resolver.attribution import pretty_source
from core.semantic.models import (
    Diagnostic,
    Diagnostics,
    ModRef,
    OverrideChain,
    PropertySnapshot,
    RootRef,
    SectionFindPage,
    SectionPage,
    SectionSnapshot,
    SourceRef,
    ValueSource,
    ValueSourceInterner,
)


class _RetainingSet(set):
    """Множество, которое переживает ``clear()`` в конце ``load()``."""

    def clear(self):
        return None


class _RetainingDict(dict):
    """Словарь, который переживает ``clear()`` в конце ``load()``."""

    def clear(self):
        return None


def _snapshot_items(items):
    """Снять значения ``Item`` в неизменяемые кортежи.

    Проектный overlay может изменить ``Item.second``/``filename`` уже
    после ``load()``. Снимок должен хранить исходное объявление, иначе
    провенанс потеряет старую строку.
    """
    return tuple(
        (
            getattr(item, "first", None),
            getattr(item, "second", None),
            getattr(item, "filename", ""),
            int(getattr(item, "depth", 0) or 0),
            int(getattr(item, "insertion_index", 0) or 0),
        )
        for item in (items or ())
    )


def _raw(item, index, name, default=None):
    """Поле снимка ``Item`` или обычного объекта."""
    if isinstance(item, tuple):
        return item[index] if index < len(item) else default
    return getattr(item, name, default)


class SemanticProvenanceMixin:
    """Mixin для экземпляра ``Resolver4.LtxResolver``.

    Наследник создаётся в адаптере: ``type(..., (Mixin, LtxResolver), {})``.
    Это не меняет ни один файл движка и не подменяет его методы вычисления.
    """

    def __init__(self, *args, **kwargs):
        self._ace_raw_base = {}
        self._ace_raw_override = {}
        self._ace_mod_ops = {}
        self._ace_parents = {}
        self._ace_base_files = {}
        self._ace_override_files = {}

        super().__init__(*args, **kwargs)

        # Предупреждения о несуществующих родителях нужны диагностике,
        # но не меняют ни данные, ни порядок вычисления.
        self.print_dltx_warnings = True
        self.sections_to_delete = _RetainingSet(
            getattr(self, "sections_to_delete", ())
        )
        self.section_to_filename = _RetainingDict(
            getattr(self, "section_to_filename", {})
        )
        self.override_to_filenames = _RetainingDict(
            getattr(self, "override_to_filenames", {})
        )

    # -------------------------------------------------- запись прохода
    def _stash_current_section(self, current_base, current_override,
                               current_file_name):
        file_name = current_file_name
        if isinstance(file_name, (list, tuple)):
            file_name = file_name[0] if file_name else ""
        if current_base is not None:
            self._ace_base_files.setdefault(
                current_base.name,
                str(file_name or ""),
            )
        if current_override is not None:
            self._ace_override_files[current_override.name] = str(
                file_name or ""
            )
        return super()._stash_current_section(
            current_base,
            current_override,
            current_file_name,
        )

    def _sort_and_filter_section(self, sect):
        # Снимок ДО фильтрации: иначе проигравшие объявления теряются и
        # цепочку переопределений восстановить нельзя.
        if self.base_data.get(sect.name) is sect:
            self._ace_raw_base[sect.name] = _snapshot_items(sect.data)
        elif self.override_data.get(sect.name) is sect:
            self._ace_raw_override[sect.name] = _snapshot_items(sect.data)
        return super()._sort_and_filter_section(sect)

    def _evaluate_section(self, section_name, resolved_cache,
                          recursion_stack):
        mods = _snapshot_items(
            self.override_modify_list_data.get(section_name, ())
        )
        if mods:
            self._ace_mod_ops[section_name] = mods

        result = super()._evaluate_section(
            section_name,
            resolved_cache,
            recursion_stack,
        )

        parents = self.base_parent_data_map.get(section_name, ())
        self._ace_parents[section_name] = tuple(parents)
        return result


@dataclass(frozen=True, slots=True)
class _Declaration:
    """Внутренняя запись об объявлении; не публичная модель."""

    section: str
    key: str
    value: Optional[str]
    file: Path
    depth: int
    insertion_index: int
    layer: str
    operation: str


def _is_delete(value) -> bool:
    return (
        type(value).__name__ == "DLTXToken"
        and getattr(value, "name", "") == "DELETE"
    )


def _value_of(value) -> Optional[str]:
    if value is None:
        return None
    if type(value).__name__ == "DLTXToken":
        return None
    return str(value)


def _merge_parent_marker(name: str) -> str:
    return name[1:] if name.startswith("!") else name


def _mod_from_display(display: str) -> Optional[ModRef]:
    if not display:
        return None
    parts = str(display).replace("/", "\\").split("\\")
    low = [part.lower() for part in parts]
    if low and low[0] == "mo2" and len(parts) > 1:
        return ModRef(parts[1])
    if "mods" in low:
        index = low.index("mods")
        if index + 1 < len(parts):
            return ModRef(parts[index + 1])
    return None


class ProvenanceStore:
    """Компактное хранилище объявлений плюс ленивый индекс секций."""

    def __init__(self, resolver, root_ref: RootRef, source_map=None,
                 abs_map=None):
        if not hasattr(resolver, "_ace_raw_base"):
            raise TypeError(
                "ProvenanceStore ожидает инструментированный Resolver4"
            )

        self.root = root_ref
        self._root_path = Path(root_ref.path)
        self._raw_base = dict(getattr(resolver, "_ace_raw_base", {}) or {})
        self._raw_override = dict(
            getattr(resolver, "_ace_raw_override", {}) or {}
        )
        self._mod_ops = dict(getattr(resolver, "_ace_mod_ops", {}) or {})
        self._parents = {
            name: tuple(
                _merge_parent_marker(parent)
                for parent in (parents or ())
                if parent
            )
            for name, parents in (
                getattr(resolver, "_ace_parents", {}) or {}
            ).items()
        }
        # ``resolver.data`` — итог после overlay-правок. Для модели важно
        # именно это состояние: иначе overlay-значения не попадут в
        # свойства.
        self._final = {
            str(name): tuple(getattr(sect, "data", ()) or ())
            for name, sect in (getattr(resolver, "data", ()) or ())
        }
        self._deleted = frozenset(
            getattr(resolver, "sections_to_delete", ()) or ()
        )
        self._warnings = tuple(getattr(resolver, "warnings", ()) or ())
        self._section_files = {
            name: str(value)
            for name, value in (
                getattr(resolver, "section_to_filename", {}) or {}
            ).items()
            if value
        }
        self._override_files = dict(
            getattr(resolver, "_ace_override_files", {}) or {}
        )
        self._source_map = {
            str(key).lower(): str(value)
            for key, value in (source_map or {}).items()
        }
        self._abs_map = {
            str(key).lower(): Path(value)
            for key, value in (abs_map or {}).items()
            if value is not None
        }

        self._interner = ValueSourceInterner()
        self._decl_index: Dict[str, Dict[str, Tuple[_Declaration, ...]]] = {}
        self._mod_index: Dict[str, Dict[str, Tuple[_Declaration, ...]]] = {}
        self._diagnostics = self._build_diagnostics()

    # -------------------------------------------------- диагностика
    @staticmethod
    def _warning_code(message: str) -> str:
        low = str(message).lower()
        if "attempted to override section" in low:
            return "override_missing_section"
        if "inherits from non-existent section" in low:
            return "missing_parent"
        if "has parent" in low and "defined as override" in low:
            return "parent_is_override"
        return "resolver_warning"

    def _warning_diagnostic(self, message: str) -> Diagnostic:
        text = str(message)
        code = self._warning_code(text)
        severity = "warning"
        section = None

        if code in (
            "override_missing_section",
            "missing_parent",
            "parent_is_override",
        ):
            match = re.search(r"section '([^']+)'", text, re.IGNORECASE)
            if match:
                section = match.group(1).lower()

        if code == "override_missing_section":
            name = section or ""
            if (
                name
                and name in self._section_files
                and not self._raw_base.get(name)
            ):
                # @[section]: Resolver4 сообщает об override до того, как
                # создаст секцию в конце LTXLoad. Это создание, не ошибка.
                code = "section_created"
                severity = "info"

        return Diagnostic(
            code=code,
            message=text,
            severity=severity,
            section=section,
        )

    def _build_diagnostics(self) -> Diagnostics:
        items = []

        for message in self._warnings:
            items.append(self._warning_diagnostic(message))

        for name in sorted(self._deleted):
            items.append(
                Diagnostic(
                    code="section_deleted",
                    message="Секция удалена через !![%s]" % name,
                    section=name,
                )
            )

        for label, table in (
            ("base", self._raw_base),
            ("override", self._raw_override),
        ):
            for name, items_raw in table.items():
                counts = {}
                for item in items_raw:
                    key = _raw(item, 0, "first")
                    if key:
                        counts[key] = counts.get(key, 0) + 1
                duplicates = sorted(
                    key for key, count in counts.items() if count > 1
                )
                if duplicates:
                    items.append(
                        Diagnostic(
                            code="key_conflict",
                            message=(
                                "Секция '%s': несколько объявлений ключей: %s"
                                % (name, ", ".join(duplicates[:5]))
                            ),
                            section=name,
                        )
                    )

        return Diagnostics(tuple(items))

    @property
    def diagnostics(self) -> Diagnostics:
        return self._diagnostics

    # -------------------------------------------------- пути и моды
    def _file_for(self, stem) -> Path:
        raw = str(stem or "")
        if not raw:
            return self._root_path
        found = self._abs_map.get(raw.lower())
        if found is not None:
            return Path(found)
        candidate = Path(raw)
        if candidate.is_absolute():
            return candidate if candidate.suffix else candidate.with_suffix(".ltx")
        if candidate.suffix:
            return self._root_path.parent / candidate
        return self._root_path.parent / (raw + ".ltx")

    def _source_file(self, raw) -> Path:
        raw = str(raw or "")
        if not raw:
            return self._root_path
        candidate = Path(raw)
        if candidate.is_absolute():
            return candidate
        stem = candidate.stem.lower() if candidate.suffix else candidate.name.lower()
        found = self._abs_map.get(stem)
        if found is not None:
            return Path(found)
        return self._root_path.parent / raw

    def _mod_for(self, file_path: Path) -> Optional[ModRef]:
        low = str(file_path).lower()
        display = self._source_map.get(os.path.splitext(low)[0])
        if display is None:
            display = self._source_map.get(Path(file_path).stem.lower())
        return _mod_from_display(display or "")

    # -------------------------------------------------- объявления
    def _declaration(self, item, section: str, layer: str,
                     operation: Optional[str] = None) -> Optional[_Declaration]:
        key = _raw(item, 0, "first")
        if not key:
            return None
        key = str(key)
        second = _raw(item, 1, "second")

        if operation is None:
            operation = "delete" if _is_delete(second) else "set"

        return _Declaration(
            section=section,
            key=key,
            value=_value_of(second),
            file=self._file_for(_raw(item, 2, "filename", "")),
            depth=int(_raw(item, 3, "depth", 0) or 0),
            insertion_index=int(
                _raw(item, 4, "insertion_index", 0) or 0
            ),
            layer=layer,
            operation=operation,
        )

    def _section_declarations(self, section: str):
        cached = self._decl_index.get(section)
        if cached is not None:
            return cached

        index: Dict[str, list] = {}
        for layer, table in (
            ("base", self._raw_base),
            ("override", self._raw_override),
        ):
            for item in table.get(section, ()):
                declaration = self._declaration(item, section, layer)
                if declaration is not None:
                    index.setdefault(declaration.key, []).append(declaration)

        frozen = {
            key: tuple(values) for key, values in index.items()
        }
        self._decl_index[section] = frozen
        return frozen

    def _mod_declarations(self, section: str):
        cached = self._mod_index.get(section)
        if cached is not None:
            return cached

        index: Dict[str, list] = {}
        for item in self._mod_ops.get(section, ()):
            raw_key = str(_raw(item, 0, "first", "") or "")
            if not raw_key or raw_key[0] not in (">", "<"):
                continue
            if _raw(item, 1, "second") is None:
                continue
            operation = "append" if raw_key[0] == ">" else "remove"
            declaration = self._declaration(
                item,
                section,
                "modop",
                operation=operation,
            )
            if declaration is None:
                continue
            declaration = _Declaration(
                section=declaration.section,
                key=raw_key[1:],
                value=declaration.value,
                file=declaration.file,
                depth=declaration.depth,
                insertion_index=declaration.insertion_index,
                layer=declaration.layer,
                operation=declaration.operation,
            )
            index.setdefault(declaration.key, []).append(declaration)

        frozen = {
            key: tuple(values) for key, values in index.items()
        }
        self._mod_index[section] = frozen
        return frozen

    @staticmethod
    def _best(declarations: Iterable[_Declaration]) -> Optional[_Declaration]:
        values = list(declarations)
        if not values:
            return None
        values.sort(
            key=lambda item: (
                item.key,
                item.depth,
                -item.insertion_index,
            )
        )
        return values[0]

    def _chain(self, section: str, key: str,
               seen=None) -> Tuple[_Declaration, ...]:
        seen = set() if seen is None else seen
        if section in seen:
            return ()
        seen = set(seen)
        seen.add(section)

        chain = []
        for parent in self._parents.get(section, ()):
            chain.extend(self._chain(parent, key, seen))

        local = self._section_declarations(section)
        # Локальные base и override хранятся в одном индексе; разделение
        # нужно, чтобы override всегда шёл после base.
        base = self._best(
            item for item in local.get(key, ()) if item.layer == "base"
        )
        if base is not None:
            chain.append(base)

        override = self._best(
            item for item in local.get(key, ())
            if item.layer == "override"
        )
        if override is not None:
            chain.append(override)

        chain.extend(self._mod_declarations(section).get(key, ()))
        return tuple(chain)

    # -------------------------------------------------- свойства и секции
    def _source(self, declaration: _Declaration) -> ValueSource:
        layer = (
            "raw"
            if declaration.layer in ("base", "override", "modop")
            else declaration.layer
        )
        return self._interner.intern(
            ValueSource(
                file=declaration.file,
                section=declaration.section,
                key=declaration.key,
                mod=self._mod_for(declaration.file),
                priority=declaration.depth,
                root=self.root,
                depth=declaration.depth,
                line=0,
                layer=layer,
                operation=declaration.operation,
            )
        )

    def _synthetic_source(self, section: str, key: str, item,
                          layer: str = "resolved") -> ValueSource:
        file_path = self._file_for(getattr(item, "filename", ""))
        return self._interner.intern(
            ValueSource(
                file=file_path,
                section=section,
                key=key,
                mod=self._mod_for(file_path),
                priority=int(getattr(item, "depth", 0) or 0),
                root=self.root,
                depth=int(getattr(item, "depth", 0) or 0),
                line=0,
                layer=layer,
                operation="set",
            )
        )

    def _kind(self, section: str) -> str:
        has_base = bool(self._raw_base.get(section))
        has_override = bool(self._raw_override.get(section))

        if has_base:
            return "override" if has_override else "base"
        if has_override:
            # @[section] Resolver4 создаёт после загрузки модов; обычный
            # override несуществующей секции в section_to_filename не
            # попадает.
            return "create" if section in self._section_files else "override"
        return "create" if section not in self._section_files else "base"

    def _declaration_file(self, section: str) -> Path:
        raw = (
            self._section_files.get(section)
            or self._override_files.get(section)
            or ""
        )
        return self._source_file(raw)

    def _winner_file(self, section: str) -> Path:
        raw = (
            self._override_files.get(section)
            or self._section_files.get(section)
            or ""
        )
        return self._source_file(raw)

    def build_property(self, section: str, key: str,
                       item) -> PropertySnapshot:
        chain = self._chain(section, key)
        sources = tuple(self._source(declaration) for declaration in chain)
        final_value = _value_of(getattr(item, "second", None))
        has_list_op = any(
            declaration.operation in ("append", "remove")
            for declaration in chain
        )

        if not sources:
            synthetic = self._synthetic_source(
                section,
                key,
                item,
                layer="patch",
            )
            sources = (synthetic,)
            winner = synthetic
            confidence = "heuristic"
        else:
            winner = sources[-1]
            winner_declaration = chain[-1]
            for source, declaration in zip(sources, chain):
                if declaration.operation != "delete":
                    winner = source
                    winner_declaration = declaration

            same_origin = (
                winner_declaration.file == self._file_for(
                    getattr(item, "filename", "")
                )
                and winner_declaration.depth == int(
                    getattr(item, "depth", 0) or 0
                )
                and winner_declaration.value == final_value
            )

            if (
                not has_list_op
                and (not same_origin or winner_declaration.operation == "delete")
            ):
                # Overlay изменил значение, но в движке остался старый
                # источник. Победитель — patch-слой, а не старая строка.
                synthetic = self._synthetic_source(
                    section,
                    key,
                    item,
                    layer="patch",
                )
                sources = sources + (synthetic,)
                winner = synthetic
                confidence = "probable"
            elif has_list_op:
                confidence = "probable"
            else:
                confidence = "exact"

        operation = winner.operation
        if self._kind(section) == "create" and operation == "set":
            operation = "create"

        return PropertySnapshot(
            key=key,
            operation=operation,
            value=final_value,
            winner=winner,
            chain=OverrideChain(sources),
            confidence=confidence,
        )

    def build_section(self, name: str) -> Optional[SectionSnapshot]:
        name = str(name or "").lower()
        items = self._final.get(name)
        if items is None:
            return None

        properties = []
        for item in items:
            key = getattr(item, "first", None)
            if not key:
                continue
            properties.append(
                self.build_property(name, str(key), item)
            )
        properties.sort(key=lambda item: item.key.lower())

        declaration_file = self._declaration_file(name)
        source_file = self._winner_file(name)
        declaration = SourceRef(
            file=declaration_file,
            section=name,
            line=0,
            mod=self._mod_for(declaration_file),
        )
        source = SourceRef(
            file=source_file,
            section=name,
            line=0,
            mod=self._mod_for(source_file),
        )
        return SectionSnapshot(
            name=name,
            declaration=declaration,
            kind=self._kind(name),
            parents=tuple(self._parents.get(name, ())),
            properties=tuple(properties),
            source=source,
        )


def render_sections(root_path, resolver_script, sections, source_map=None,
                    abs_map=None, include_text=True):
    """Текст и построчные источники из итоговых секций.

    ``sections`` — последовательность ``(name, items)`` в порядке
    резолвера. Это общая проекция для полной модели и для быстрого
    ``assemble_resolved``: отдельного второго рендера нет.
    """
    root_path = Path(root_path)
    source_map = source_map or {}
    abs_map = abs_map or {}
    root_stem = root_path.stem.lower()

    src_cache = {}

    def src_of(filename):
        if not filename:
            return None
        cached = src_cache.get(filename)
        if cached is not None:
            return cached
        if filename == root_stem:
            source = LineSource(
                kind="original",
                display="",
                path=root_path,
                open_raw=False,
            )
        else:
            full = abs_map.get(filename)
            source = LineSource(
                kind="file",
                display=pretty_source(filename, source_map),
                path=Path(full) if full is not None else None,
                open_raw=False,
            )
        src_cache[filename] = source
        return source

    lines = [] if include_text else None
    sources = []

    def emit(line, source):
        if lines is not None:
            lines.append(line)
        sources.append(source)

    emit("; Assembled root: %s" % root_path, None)
    emit("; Resolver module: %s" % resolver_script, None)
    emit("", None)

    for section_name, items in sections:
        section_source = None
        for item in items:
            filename = getattr(item, "filename", "")
            if filename:
                section_source = src_of(filename)
                break

        emit("[%s]" % section_name, section_source)

        for item in items:
            key = str(getattr(item, "first", "") or "")
            value = getattr(item, "second", "")
            if value is None:
                value_text = ""
            elif (
                type(value).__name__ == "DLTXToken"
                and hasattr(value, "name")
            ):
                value_text = "<%s>" % value.name
            else:
                value_text = str(value)

            item_source = src_of(getattr(item, "filename", ""))

            if "\n" in value_text or "\r" in value_text:
                parts = (
                    value_text.replace("\r\n", "\n")
                    .replace("\r", "\n")
                    .split("\n")
                )
                emit("%s = %s" % (key, parts[0]), item_source)
                for part in parts[1:]:
                    emit(part, None)
            elif value_text == "":
                emit("%s =" % key, item_source)
            else:
                emit("%s = %s" % (key, value_text), item_source)

        emit("", None)

    text = "\n".join(lines) if lines is not None else None
    return text, tuple(sources)


class SectionIndex:
    """Ленивый индекс секций одного ``ResolvedRoot``."""

    def __init__(self, store: ProvenanceStore):
        self._store = store
        self._names = tuple(sorted(store._final))
        self._by_name = {name.lower(): name for name in self._names}

    @property
    def names(self) -> Tuple[str, ...]:
        return self._names

    def key_counts(self) -> dict:
        """Количество итоговых ключей по секциям без построения свойств."""
        return {
            name: len(items)
            for name, items in self._store._final.items()
        }

    @property
    def diagnostics(self) -> Diagnostics:
        return self._store.diagnostics

    def render(self, root_path, resolver_script, source_map=None,
               abs_map=None, include_text=True):
        """Текст и ``line_sources`` как проекция итоговых секций модели."""
        return render_sections(
            root_path,
            resolver_script,
            self._store._final.items(),
            source_map=source_map,
            abs_map=abs_map,
            include_text=include_text,
        )

    def get(self, name: str) -> Optional[SectionSnapshot]:
        normalized = str(name or "").lower()
        actual = self._by_name.get(normalized)
        if actual is None:
            return None
        return self._store.build_section(actual)

    def find(self, query: str, exact: bool = False, offset: int = 0,
             limit: int = 100) -> SectionFindPage:
        text = str(query or "").lower().strip()
        if exact:
            found = [name for name in self._names if name == text]
        else:
            found = [name for name in self._names if text in name]

        offset = max(0, int(offset or 0))
        limit = max(0, int(limit if limit is not None else 100))
        page = found[offset:offset + limit]
        return SectionFindPage(
            names=tuple(page),
            total=len(found),
            offset=offset,
            limit=limit,
        )

    def properties(self, name: str, keys=None, offset: int = 0,
                   limit: int = 100) -> SectionPage:
        section = self.get(name)
        if section is None:
            return SectionPage(
                section=None,
                properties=(),
                total=0,
                offset=max(0, int(offset or 0)),
                limit=max(0, int(limit if limit is not None else 100)),
            )

        values = section.properties
        if keys is not None:
            wanted = {str(key).lower() for key in keys}
            values = tuple(
                item for item in values if item.key.lower() in wanted
            )

        offset = max(0, int(offset or 0))
        limit = max(0, int(limit if limit is not None else 100))
        return SectionPage(
            section=section,
            properties=tuple(values[offset:offset + limit]),
            total=len(values),
            offset=offset,
            limit=limit,
            diagnostics=self._store.diagnostics,
        )

    def iter_dump(self, section: Optional[str] = None):
        """Построчный дамп модели; генератор не материализует весь корень."""
        names = (
            (str(section).lower(),)
            if section is not None
            else self._names
        )
        yield "section\tkey\tvalue\tfile\tdepth\toperation\tconfidence"
        for name in names:
            snapshot = self.get(name)
            if snapshot is None:
                continue
            for prop in snapshot.properties:
                value = (
                    "" if prop.value is None
                    else str(prop.value)
                    .replace("\r", " ")
                    .replace("\n", "\\n")
                    .replace("\t", " ")
                )
                yield "%s\t%s\t%s\t%s\t%s\t%s\t%s" % (
                    snapshot.name,
                    prop.key,
                    value,
                    prop.winner.file,
                    "" if prop.winner.depth is None else prop.winner.depth,
                    prop.operation,
                    prop.confidence,
                )

    def dump(self, section: Optional[str] = None) -> str:
        return "\n".join(self.iter_dump(section=section))


__all__ = [
    "ProvenanceStore",
    "SectionIndex",
    "SemanticProvenanceMixin",
]
