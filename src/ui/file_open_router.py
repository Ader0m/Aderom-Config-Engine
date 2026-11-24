# -*- coding: utf-8 -*-
"""Маршрутизатор открытия файлов."""

import logging
from pathlib import Path

from sdk.ui import FileOpenHandler
from sdk.kernel.registrations import RegistrationHandle, remove_identity

from .platform.file_open_preferences import FileOpenPreferences

log = logging.getLogger(__name__)


def _normalize_context(context):
    value = str(context or "view").strip().lower()
    if value not in FileOpenHandler.CONTEXTS:
        raise ValueError(
            "Неизвестный контекст открытия: %r (ожидалось %s)"
            % (context, ", ".join(FileOpenHandler.CONTEXTS))
        )
    return value


def _suffix_key(path):
    return Path(path).suffix.lower()


class TextEditorHandler(FileOpenHandler):
    """Обработчик по умолчанию: текстовый редактор (сырой файл)."""

    contexts = FileOpenHandler.CONTEXTS
    priority = 0

    def __init__(self, editor):
        self.editor = editor

    def open(self, path, api):
        api.log("Открытие файла: %s" % Path(path).name)
        text = api.document_text(path)

        if not api.can_edit_documents():
            api.set_status(
                "Проект не создан: файл открыт только для чтения",
                5000,
            )

        request_id = int(api.current_file_request_id() or 0)
        return bool(
            self.editor.open_file(
                str(path),
                text=text,
                request_id=request_id,
            )
        )


class _HandlerEntry:
    """Обработчик открытия вместе с владельцем-модулем."""

    __slots__ = ("handler", "context", "owner", "valid", "key")

    def __init__(self, handler, context, owner):
        self.handler = handler
        self.context = context
        self.owner = str(owner or "")
        self.valid = _handler_valid(handler)
        self.key = ""

    def supports_context(self, context):
        if not self.valid:
            return False
        try:
            return bool(self.handler.supports_context(context))
        except Exception:
            log.exception(
                "supports_context обработчика открытия упал: %r",
                self.handler,
            )
            return False


def _handler_valid(handler):
    if not isinstance(handler, FileOpenHandler):
        return False
    try:
        values = tuple(handler.contexts)
    except Exception:
        log.exception(
            "Не удалось прочитать contexts обработчика открытия %r",
            handler,
        )
        return False
    if not values:
        log.warning(
            "Обработчик открытия %r не объявил contexts и не будет "
            "участвовать в маршрутизации",
            handler,
        )
        return False
    invalid = [
        value
        for value in values
        if str(value).strip().lower() not in FileOpenHandler.CONTEXTS
    ]
    if invalid:
        log.warning(
            "Обработчик открытия %r содержит недопустимые contexts %r "
            "и не будет участвовать в маршрутизации",
            handler,
            invalid,
        )
        return False
    handler_id = str(handler.handler_id or "").strip()
    if "|" in handler_id or "#" in handler_id:
        log.warning(
            "Обработчик открытия %r содержит недопустимый handler_id %r "
            "(символы '|' и '#' запрещены)",
            handler,
            handler.handler_id,
        )
        return False
    return True


def _priority(handler):
    try:
        return int(getattr(handler, "priority", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _base_handler_key(entry):
    handler = entry.handler
    kind = "%s.%s" % (
        type(handler).__module__,
        type(handler).__qualname__,
    )
    handler_id = ""
    if isinstance(handler, FileOpenHandler):
        handler_id = str(handler.handler_id or "").strip()
    suffix = "#%s" % handler_id if handler_id else ""
    return "%s|%s%s" % (entry.owner or "<platform>", kind, suffix)


def _handler_key(entry):
    return entry.key


def _handler_label(entry):
    handler = entry.handler
    owner = entry.owner or "<platform>"
    return "%s(key=%s, owner=%s, priority=%s)" % (
        type(handler).__name__,
        entry.key,
        owner,
        _priority(handler),
    )


class FileOpenRouter:
    """Выбирает обработчик открытия файла.

    Маршрутизатор только принимает запрос. Факт открытия публикует UI-слой
    (``EditorTabs``) после того, как виджет размещён и стал текущим.
    """

    def __init__(self, preferences_path=None):
        self._handlers = []  # [_HandlerEntry]
        self.default = None
        self.preferences = FileOpenPreferences(preferences_path)
        self._chooser = None

    def register(self, handler, context=None, owner=None):
        """Зарегистрировать обработчик и вернуть handle для снятия."""
        if not isinstance(handler, FileOpenHandler):
            log.warning(
                "Обработчик открытия %r не наследует FileOpenHandler "
                "и не будет участвовать в маршрутизации",
                handler,
            )
        entry = _HandlerEntry(handler, context, owner)
        entry.key = _base_handler_key(entry)
        if entry.valid and any(
            item.valid and item.key == entry.key
            for item in self._handlers
        ):
            log.warning(
                "Обработчик открытия %r дублирует ключ %r; задайте "
                "уникальный handler_id",
                handler,
                entry.key,
            )
            entry.valid = False
        self._handlers.append(entry)
        return RegistrationHandle(
            lambda: self._remove(entry),
            entry.owner,
        )

    def unregister(self, handler, owner=None):
        """Снять обработчик. ``owner=None`` — внутренняя очистка платформы."""
        wanted = None if owner is None else str(owner)
        for entry in tuple(self._handlers):
            if entry.handler is not handler:
                continue
            if wanted is not None and entry.owner != wanted:
                continue
            return self._remove(entry)
        return False

    def _remove(self, entry):
        return remove_identity(self._handlers, entry)

    def set_default(self, handler):
        self.default = handler

    def set_chooser(self, chooser):
        """Задать обработчик вопроса при неоднозначности.

        ``chooser(path, context, options)`` получает путь, контекст и
        кортеж ``(key, label)``. Он возвращает ключ выбранного обработчика
        или ``None``. Выбор запоминается как предпочтение для расширения и
        контекста.
        """
        self._chooser = chooser

    def set_preference(self, suffix, context, handler_key):
        self.preferences.set(
            str(suffix or "").lower(),
            _normalize_context(context),
            handler_key,
        )

    def preference(self, suffix, context):
        return self.preferences.get(
            str(suffix or "").lower(),
            _normalize_context(context),
        )

    def clear_preference(self, suffix, context):
        return self.preferences.clear(
            str(suffix or "").lower(),
            _normalize_context(context),
        )

    def open(self, path, api, context="view"):
        context = _normalize_context(context)
        p = Path(path)
        entries = sorted(
            (
                entry
                for entry in tuple(self._handlers)
                if entry.supports_context(context)
            ),
            key=lambda item: -_priority(item.handler),
        )

        index = 0
        while index < len(entries):
            priority = _priority(entries[index].handler)
            group = []
            while (
                index < len(entries)
                and _priority(entries[index].handler) == priority
            ):
                group.append(entries[index])
                index += 1

            candidates = []
            for entry in group:
                try:
                    if entry.handler.can_open(p):
                        candidates.append(entry)
                except Exception:
                    log.exception(
                        "can_open обработчика открытия упал: %r",
                        entry.handler,
                    )

            if not candidates:
                continue

            entry = self._choose_candidate(candidates, p, context)
            handler = entry.handler
            module_context = entry.context
            try:
                # Модульный обработчик получает свой ModuleContext, а не
                # сырой PlatformApi. Платформенный default — PlatformApi.
                result = handler.open(
                    p,
                    module_context if module_context is not None else api,
                )
                # False означает «обработчик выбран, но открытие не
                # удалось». Не подменяем ошибку открытием сырого файла.
                return bool(result)
            except Exception:
                log.exception("Обработчик открытия файла упал: %r", handler)
                # Обработчик уже выбран по типу файла; не подменяем его
                # ошибку открытием сырого файла по умолчанию.
                return False

        return self.open_default(p, api, context=context)

    def _choose_candidate(self, candidates, path, context):
        if len(candidates) == 1:
            return candidates[0]

        preferred_key = self.preference(_suffix_key(path), context)
        if preferred_key:
            for entry in candidates:
                if _handler_key(entry) == preferred_key:
                    return entry

        if self._chooser is not None:
            options = tuple(
                (_handler_key(entry), _handler_label(entry))
                for entry in candidates
            )
            try:
                choice = self._chooser(path, context, options)
            except Exception:
                log.exception("Вопрос о выборе обработчика открытия упал")
                choice = None
            if choice:
                for entry in candidates:
                    if _handler_key(entry) == str(choice):
                        self.set_preference(
                            _suffix_key(path),
                            context,
                            choice,
                        )
                        return entry
                log.warning(
                    "Выбран неизвестный обработчик открытия %r для %s",
                    choice,
                    path,
                )

        log.warning(
            "Конфликт обработчиков открытия %s на priority=%d: %s; "
            "выбран %s",
            path,
            _priority(candidates[0].handler),
            ", ".join(_handler_label(item) for item in candidates),
            _handler_label(candidates[0]),
        )
        return candidates[0]

    def open_default(self, path, api, context="view"):
        """Открыть в обработчике по умолчанию, минуя зарегистрированные."""
        _normalize_context(context)
        p = Path(path)
        if self.default is None:
            return False
        try:
            return bool(self.default.open(p, api))
        except Exception:
            log.exception("Обработчик открытия по умолчанию упал")
            return False
