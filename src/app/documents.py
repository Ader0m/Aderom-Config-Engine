# -*- coding: utf-8 -*-
"""Живые документы прикладного слоя.

Один :class:`ResolvedDocument` — единственный владелец состояния
resolved-файла: текста, карты источников, ревизии и признака изменений.
Все представления (resolved-вид, карточка предмета, preview) являются
проекциями документа и не хранят собственных копий.

Документ намеренно не знает про Qt и про проект: сохранение и запись
черновика передаются колбэками. Это позволяет тестировать его как
обычный Python-объект.
"""

from __future__ import annotations

import bisect
import logging
import re
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from core import parse_ltx_document
from sdk.api import ResolvedDraftInfo
from sdk.events import Events

log = logging.getLogger(__name__)


_HEADER_RE = re.compile(r"^[ \t]*(?:!!|!|@)?\[([^\]\r\n]+)\]")
_FIELD_PREFIXES = ("!", ">", "<")


def normalize_text(text) -> str:
    """Привести переводы строк к ``\\n`` (внутренний формат документа)."""
    value = str(text or "")
    if "\r" not in value:
        return value
    return value.replace("\r\n", "\n").replace("\r", "\n")


def header_name(line) -> Optional[str]:
    """Имя секции из строки заголовка LTX (``[x]``, ``![x]``, ``@[x]``)."""
    match = _HEADER_RE.match(str(line or ""))
    if match is None:
        return None
    name = match.group(1).strip().lower()
    return name or None


def field_key(line) -> str:
    """Ключ из строки ``ключ = значение`` (с учётом префиксов DLTX)."""
    value = str(line or "").strip()
    for prefix in _FIELD_PREFIXES:
        if value.startswith(prefix):
            value = value[1:].lstrip()
            break
    if "=" in value:
        value = value.split("=", 1)[0]
    return value.strip().lower()


def collapse_duplicated_text(text, original=""):
    """Схлопнуть ``text + text`` в один экземпляр.

    Это инвариант предметной модели: resolved-текст не может быть точной
    двойной копией самого себя. Вторая половина должна быть близка по
    размеру к original, иначе замена считается легитимной.
    """
    value = str(text or "")
    original = str(original or "")
    if not value or not original or len(value) % 2:
        return value
    half = len(value) // 2
    if value[:half] != value[half:]:
        return value
    tolerance = max(1024, int(len(original) * 0.02))
    if abs(half - len(original)) > tolerance:
        return value
    return value[:half]


def fit_sources(sources, count):
    """Дополнить/обрезать карту источников до числа строк."""
    values = tuple(sources or ())
    if len(values) == count:
        return values
    result = list(values)
    if len(result) < count:
        result.extend([None] * (count - len(result)))
    else:
        del result[count:]
    return tuple(result)


def apply_line_edit(
    text,
    line_sources,
    action,
    line_number,
    new_line="",
):
    """Чистая правка одной строки.

    Возвращает ``(new_text, new_sources)``. При некорректной строке
    бросает :class:`ValueError`; состояние документа не меняется.
    """
    normalized = normalize_text(text)
    lines = normalized.split("\n")
    sources = list(fit_sources(line_sources, len(lines)))

    try:
        line_number = int(line_number)
    except (TypeError, ValueError):
        raise ValueError("Некорректный номер строки: %r" % (line_number,))

    body = str(new_line or "").rstrip("\r\n")
    if "\n" in body or "\r" in body:
        raise ValueError("Правка строки не должна содержать переводы строк")

    if action == "replace":
        index = line_number - 1
        if index < 0 or index >= len(lines):
            raise ValueError("Строка %s не найдена" % line_number)
        lines[index] = body

    elif action == "delete":
        index = line_number - 1
        if index < 0 or index >= len(lines):
            raise ValueError("Строка %s не найдена" % line_number)
        del lines[index]
        del sources[index]

    elif action == "insert":
        if line_number < 0 or line_number > len(lines):
            raise ValueError("Строка %s не найдена" % line_number)
        index = line_number
        lines.insert(index, body)
        sources.insert(index, None)

    else:
        raise ValueError("Неизвестное действие правки: %r" % (action,))

    return "\n".join(lines), tuple(sources)


@dataclass
class SectionSpan:
    """Границы секции в документе (1-based, ``end`` — исключающая)."""

    name: str
    start: int
    end: int
    char_start: int = 0
    char_end: int = 0


@dataclass
class DocumentSubscription:
    """Подписка на изменения документа."""

    _callback: Callable
    _owner: "ResolvedDocument"
    closed: bool = field(default=False)

    def unsubscribe(self):
        if self.closed:
            return
        self.closed = True
        self._owner._unsubscribe(self._callback)


class ResolvedDocument:
    """Единственный владелец состояния одного resolved-файла."""

    def __init__(
        self,
        tab_id,
        root_path,
        title,
        text,
        line_sources=(),
        saved_text=None,
        original_text=None,
        events=None,
        persist=None,
        save=None,
        log_fn=None,
        document_format="ltx",
    ):
        self.tab_id = str(tab_id or "")
        self.root_path = Path(root_path) if root_path is not None else None
        self.title = str(title or "")
        self.document_format = str(document_format or "ltx").lower()

        normalized = normalize_text(text)
        original = normalize_text(
            normalized if original_text is None else original_text
        )
        normalized = collapse_duplicated_text(normalized, original)
        if saved_text is None:
            saved = normalized
        else:
            saved = collapse_duplicated_text(
                normalize_text(saved_text),
                original,
            )
        self._text = normalized
        self._line_sources = fit_sources(
            line_sources,
            self._text.count("\n") + 1,
        )
        self._saved_text = saved
        self._original_text = original
        self.revision = 0
        self.persist_error = False

        self._events = events
        self._persist = persist
        self._save = save
        self._log_fn = log_fn or (lambda message: None)

        self._view = None
        self._subscribers = []
        self._clients = 0

        self._spans = None
        self._span_by_name = None
        self._parsed = None

        # История нужна только когда текстового представления нет:
        # при открытом ResolvedView undo живёт в самом Scintilla.
        self._undo_stack = []
        self._redo_stack = []

    # -------------------------------------------------- состояние
    @property
    def current_text(self) -> str:
        return self._text

    @property
    def saved_text(self) -> str:
        return self._saved_text

    @property
    def original_text(self) -> str:
        return self._original_text

    @property
    def line_sources(self):
        return self._line_sources

    @property
    def is_dirty(self) -> bool:
        return self._text != self._saved_text

    @property
    def has_changes(self) -> bool:
        return self._text != self._original_text

    @property
    def line_count(self) -> int:
        return self._text.count("\n") + 1

    @property
    def is_xml(self) -> bool:
        return self.document_format == "xml"

    def snapshot(self) -> ResolvedDraftInfo:
        return ResolvedDraftInfo(
            text=self._text,
            saved_text=self._saved_text,
            original_text=self._original_text,
            line_sources=tuple(self._line_sources),
        )

    # -------------------------------------------------- подписки
    def subscribe(self, callback) -> DocumentSubscription:
        self._subscribers.append(callback)
        return DocumentSubscription(callback, self)

    def _unsubscribe(self, callback):
        try:
            self._subscribers.remove(callback)
        except ValueError:
            pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def retain(self):
        """Удержать документ, пока им пользуется внешнее представление."""
        self._clients += 1

    def release(self):
        self._clients = max(0, self._clients - 1)

    @property
    def client_count(self) -> int:
        return self._clients

    def can_drop(self) -> bool:
        return (
            self._view is None
            and not self._subscribers
            and self._clients <= 0
        )

    # -------------------------------------------------- представления
    def bind_view(self, view):
        """Привязать текстовое представление (ResolvedView)."""
        self._view = view
        # У представления своя история; документная больше не нужна.
        self._undo_stack.clear()
        self._redo_stack.clear()

    def unbind_view(self, view=None):
        if view is None or self._view is view:
            self._view = None
        self._undo_stack.clear()
        self._redo_stack.clear()

    @property
    def has_view(self) -> bool:
        return self._view is not None

    # -------------------------------------------------- изменение текста
    def replace_text(
        self,
        text,
        line_sources=None,
        origin=None,
        sections=(),
        persist=True,
        atomic=False,
        focus_line=None,
        full=False,
    ) -> bool:
        """Заменить весь текст (ввод в редакторе, перезагрузка проекта)."""
        normalized = normalize_text(text)

        # Инвариант: документ не может внезапно стать своей точной
        # двойной копией. Это защищает от ошибочного append во view и
        # не даёт такому тексту уйти в overlay/Resolver4.
        if (
            self._text
            and len(normalized) == len(self._text) * 2
            and normalized == self._text + self._text
        ):
            log.error(
                "ResolvedDocument %s: отклонена замена текста его "
                "двойной копией.\n%s",
                self.tab_id,
                "".join(traceback.format_stack(limit=16)),
            )
            return False

        if line_sources is None:
            sources = fit_sources(
                self._line_sources,
                normalized.count("\n") + 1,
            )
        else:
            sources = fit_sources(
                line_sources,
                normalized.count("\n") + 1,
            )

        if normalized == self._text and sources == self._line_sources:
            return False

        old = (self._text, self._line_sources)
        self._text = normalized
        self._line_sources = sources
        self._spans = None
        self._span_by_name = None
        self._parsed = None
        self.revision += 1

        if persist and not self._persist_state(atomic=atomic):
            if atomic:
                self._text, self._line_sources = old
                self.revision -= 1
                self._spans = None
                self._span_by_name = None
                self._parsed = None
                return False

        self._publish(
            sections,
            origin=origin,
            full=full,
            focus_line=focus_line or 0,
        )
        return True

    def apply_line_edit(
        self,
        action,
        line_number,
        text="",
        section=None,
        persist=True,
    ) -> bool:
        """Точечная правка строки с транзакционной семантикой.

        Сначала вычисляется новое состояние, затем оно фиксируется.
        Если запись черновика не удалась, состояние и представление
        остаются прежними.
        """
        try:
            new_text, new_sources = apply_line_edit(
                self._text,
                self._line_sources,
                action,
                line_number,
                text,
            )
        except ValueError as exc:
            self._log_fn(str(exc))
            return False

        if section is None:
            if action == "insert":
                section = self.section_at_line(max(1, int(line_number)))
            else:
                section = self.section_at_line(int(line_number))

        old_text = self._text
        old_sources = self._line_sources
        old_revision = self.revision
        old_spans = self._spans
        old_by_name = self._span_by_name

        self._text = new_text
        self._line_sources = new_sources
        self._spans = None
        self._span_by_name = None
        self._parsed = None
        self.revision += 1

        if persist and not self._persist_state(atomic=True):
            self._text = old_text
            self._line_sources = old_sources
            self.revision = old_revision
            self._spans = old_spans
            self._span_by_name = old_by_name
            self._parsed = None
            return False

        view = self._view
        if view is not None:
            applied = view.apply_line_edit(action, line_number, text)
            if not applied:
                # Откат уже зафиксированного состояния: представление
                # осталось прежним, значит и документ должен совпадать.
                self._text = old_text
                self._line_sources = old_sources
                self.revision = old_revision
                self._spans = old_spans
                self._span_by_name = old_by_name
                self._parsed = None
                self._persist_state(atomic=False)
                return False
            view.set_dirty_state(self.is_dirty)
        else:
            old_lines = old_text.split("\n")
            index = int(line_number) - 1
            old_line = (
                old_lines[index]
                if 0 <= index < len(old_lines)
                else ""
            )
            old_source = (
                old_sources[index]
                if 0 <= index < len(old_sources)
                else None
            )
            self._remember(
                action,
                line_number,
                old_line,
                text,
                old_source,
            )

        self._publish((section,) if section else ())
        return True

    def _persist_state(self, atomic=True) -> bool:
        if self._persist is None:
            return True
        try:
            ok = bool(self._persist(self))
        except Exception as exc:
            log.exception("Не удалось обновить resolved-черновик")
            self._log_fn("Не удалось обновить черновик: %s" % exc)
            ok = False
        self.persist_error = not ok
        if not ok:
            self._log_fn(
                "Resolved-черновик не обновлён: изменения остались в документе"
            )
        return ok

    # -------------------------------------------------- семантика полей
    def apply_field_edit(self, section, key, new_value, persist=True) -> bool:
        """Заменить значение поля или добавить его в секцию."""
        key = str(key or "").strip()
        if not key:
            return False
        bounds = self.section_bounds(section)
        if bounds is None:
            return False

        line = self.field_line(section, key)
        body = "%s = %s" % (key, str(new_value).rstrip("\r\n"))
        if line > 0:
            return self.apply_line_edit(
                "replace",
                line,
                body,
                section=section,
                persist=persist,
            )

        insert_after = self._insert_after_line(section)
        return self.apply_line_edit(
            "insert",
            insert_after,
            body,
            section=section,
            persist=persist,
        )

    def delete_field(self, section, key, persist=True) -> bool:
        line = self.field_line(section, key)
        if line <= 0:
            return False
        return self.apply_line_edit(
            "delete",
            line,
            section=section,
            persist=persist,
        )

    def rename_field(
        self,
        section,
        old_key,
        new_key,
        new_value,
        persist=True,
    ) -> bool:
        """Переименовать поле, найдя его по секции и старому ключу."""
        new_key = str(new_key or "").strip()
        if not new_key:
            return False
        line = self.field_line(section, old_key)
        if line <= 0:
            return self.apply_field_edit(
                section,
                new_key,
                new_value,
                persist=persist,
            )
        return self.apply_line_edit(
            "replace",
            line,
            "%s = %s" % (new_key, str(new_value).rstrip("\r\n")),
            section=section,
            persist=persist,
        )

    def insert_field(
        self,
        section,
        key,
        value,
        after_key=None,
        persist=True,
    ) -> bool:
        key = str(key or "").strip()
        if not key:
            return False
        bounds = self.section_bounds(section)
        if bounds is None:
            return False

        body = "%s = %s" % (key, str(value).rstrip("\r\n"))
        if after_key:
            line = self.field_line(section, after_key)
            if line > 0:
                return self.apply_line_edit(
                    "insert",
                    line,
                    body,
                    section=section,
                    persist=persist,
                )

        return self.apply_line_edit(
            "insert",
            self._insert_after_line(section),
            body,
            section=section,
            persist=persist,
        )

    def _insert_after_line(self, section):
        block = self.section_block(section)
        if block is None:
            return 0
        block_text, offset = block
        last = offset + 1
        for index, line in enumerate(block_text.split("\n"), 1):
            if line.strip():
                last = offset + index
        return last

    # -------------------------------------------------- индекс секций
    def _build_spans(self):
        # Единственный LTX-парсер живёт в core. Здесь только кэшируем
        # результат, чтобы навигация и правки не разбирали текст повторно.
        self._parsed = (
            None
            if self.is_xml
            else parse_ltx_document(self._text)
        )
        self._spans = []
        self._span_by_name = {}

    def _ensure_spans(self):
        if self._parsed is None and not self.is_xml:
            self._build_spans()

    def section_at_line(self, line_number) -> Optional[str]:
        """Секция, в которой находится строка (1-based), или ``None``."""
        self._ensure_spans()
        if self._parsed is None:
            return None
        try:
            return self._parsed.section_at_line(int(line_number))
        except (TypeError, ValueError):
            return None

    def section_bounds(self, section) -> Optional[tuple]:
        """Границы секции ``(start, end)``, 1-based, ``end`` исключающая."""
        self._ensure_spans()
        if self._parsed is None:
            return None
        return self._parsed.section_bounds(section)

    def section_block(self, section) -> Optional[tuple]:
        """Текст блока секции и смещение строк (без полного копирования)."""
        self._ensure_spans()
        if self._parsed is None:
            return None
        return self._parsed.section_block(section)

    def section_names(self):
        self._ensure_spans()
        if self._parsed is None:
            return ()
        result = []
        seen = set()
        for name, _line in self._parsed.headers:
            if name in seen:
                continue
            seen.add(name)
            result.append(name)
        return tuple(result)

    def field_line(self, section, key) -> int:
        """Номер строки поля в секции (последнее вхождение) или 0."""
        self._ensure_spans()
        if self._parsed is None:
            return 0
        return self._parsed.field_line(section, key)

    # -------------------------------------------------- сохранение
    def mark_saved(self, text=None) -> bool:
        new_saved = normalize_text(
            self._text if text is None else text
        )
        if new_saved == self._saved_text:
            return False
        self._saved_text = new_saved
        return True

    def save(self) -> bool:
        if self._save is None:
            return False
        try:
            ok = bool(self._save(self))
        except Exception as exc:
            log.exception("Не удалось сохранить resolved-документ")
            self._log_fn("Не удалось сохранить документ: %s" % exc)
            return False
        if ok:
            self.mark_saved()
        return ok

    def reset(
        self,
        text,
        line_sources=None,
        saved_text=None,
        original_text=None,
        persist=False,
    ) -> bool:
        """Полная перезагрузка состояния (загрузка проекта, сброс)."""
        original = (
            self._original_text
            if original_text is None
            else normalize_text(original_text)
        )
        text = collapse_duplicated_text(normalize_text(text), original)
        if saved_text is not None:
            self._saved_text = collapse_duplicated_text(
                normalize_text(saved_text),
                original,
            )
        if original_text is not None:
            self._original_text = original
        return self.replace_text(
            text,
            line_sources=line_sources,
            persist=persist,
            atomic=False,
            sections=(),
            full=True,
        )

    # -------------------------------------------------- undo/redo
    def _remember(self, action, line_number, old_line, new_line, old_source=None):
        self._undo_stack.append(
            (action, int(line_number), old_line, new_line, old_source)
        )
        self._redo_stack.clear()

    def undo(self) -> bool:
        view = self._view
        if view is not None:
            view.undo()
            return True
        if not self._undo_stack:
            return False

        entry = self._undo_stack.pop()
        action, line_number, old_line, new_line, old_source = entry

        if action == "replace":
            ok = self._replay("replace", line_number, old_line)
        elif action == "insert":
            ok = self._replay("delete", line_number + 1, "")
        elif action == "delete":
            ok = self._replay(
                "insert",
                line_number - 1,
                old_line,
                line_source=old_source,
            )
        else:
            ok = False

        if ok:
            self._redo_stack.append(entry)
        return ok

    def redo(self) -> bool:
        view = self._view
        if view is not None:
            view.redo()
            return True
        if not self._redo_stack:
            return False

        entry = self._redo_stack.pop()
        action, line_number, old_line, new_line, old_source = entry

        if action == "replace":
            ok = self._replay("replace", line_number, new_line)
        elif action == "insert":
            ok = self._replay("insert", line_number, new_line)
        elif action == "delete":
            ok = self._replay("delete", line_number, "")
        else:
            ok = False

        if ok:
            self._undo_stack.append(entry)
        return ok

    def _replay(self, action, line_number, text, line_source=None) -> bool:
        """Применить правку истории, не записывая её снова."""
        try:
            new_text, new_sources = apply_line_edit(
                self._text,
                self._line_sources,
                action,
                line_number,
                text,
            )
        except ValueError:
            return False

        if (
            action == "insert"
            and line_source is not None
            and 0 <= int(line_number) < len(new_sources)
        ):
            sources = list(new_sources)
            sources[int(line_number)] = line_source
            new_sources = tuple(sources)

        old_text = self._text
        old_sources = self._line_sources
        old_revision = self.revision

        self._text = new_text
        self._line_sources = new_sources
        self._spans = None
        self._span_by_name = None
        self._parsed = None
        self.revision += 1

        if not self._persist_state(atomic=True):
            self._text = old_text
            self._line_sources = old_sources
            self.revision = old_revision
            self._spans = None
            self._span_by_name = None
            self._parsed = None
            return False

        view = self._view
        if view is not None:
            view.apply_line_edit(action, line_number, text)
            view.set_dirty_state(self.is_dirty)

        section = self.section_at_line(line_number)
        self._publish((section,) if section else ())
        return True

    # -------------------------------------------------- события
    def _publish(self, sections, origin=None, full=False, focus_line=0):
        sections = tuple(
            str(section).strip().lower()
            for section in (sections or ())
            if str(section or "").strip()
        )

        from sdk.api import ResolvedDocumentChange

        change = ResolvedDocumentChange(
            tab_id=self.tab_id,
            revision=self.revision,
            sections=sections,
            full=bool(full),
            focus_line=int(focus_line or 0),
        )

        for callback in list(self._subscribers):
            try:
                callback(change)
            except Exception:
                log.exception("Подписчик документа упал")

        if self._events is None:
            return

        try:
            self._events.publish(Events.RESOLVED_DOCUMENT_CHANGED, change)
        except Exception:
            log.exception("Не удалось опубликовать изменение документа")
