# -*- coding: utf-8 -*-
"""Шина событий и шина команд.

Разделение:
- **событие** — уже произошедший факт (`publish`);
- **UI-действие** — запрос, который можно перехватить (`dispatch`);
- **команда** — намерение с одним обработчиком (`CommandBus`).

Потокобезопасность: подписки и публикация защищены блокировкой. Если
``publish`` вызван не из потока Qt-приложения, подписчики получают событие
через очередь главного потока — иначе UI обновлялся бы из фонового потока.
"""

import logging
import threading
import traceback
from collections import deque

try:  # Qt нужен только для доставки событий в главный поток.
    from PyQt5.QtCore import QCoreApplication, QObject, QThread, pyqtSignal, pyqtSlot
except ImportError:  # pragma: no cover - среда без PyQt
    QCoreApplication = None
    QObject = None
    QThread = None
    pyqtSignal = None
    pyqtSlot = None

log = logging.getLogger(__name__)

try:
    from sdk.contracts.events import PLATFORM_OWNER
except ImportError:  # pragma: no cover - ui всегда импортирует sdk
    PLATFORM_OWNER = "<platform>"


class Subscription:
    """Подписка на событие. Позволяет отписаться без знания внутренностей шины."""

    def __init__(self, bus, name, entry):
        self._bus = bus
        self._name = name
        self._entry = entry
        self._active = True
        self.owner = entry.owner

    @property
    def active(self):
        return self._active

    def unsubscribe(self):
        """Отписаться. Повторный вызов безопасен."""
        if self._active:
            self._bus._remove_entry(self._name, self._entry)
            self._active = False


class _Entry:
    __slots__ = ("priority", "order", "fn", "owner", "active")

    def __init__(self, priority, order, fn, owner=PLATFORM_OWNER):
        self.priority = priority
        self.order = order
        self.fn = fn
        self.owner = str(owner or "")
        self.active = True


if QObject is not None:

    class _EventRelay(QObject):
        """Переносит вызов подписчиков в поток, где создана шина."""

        deliver = pyqtSignal()

        def __init__(self, bus):
            super().__init__()
            self._bus = bus
            # Соединение «сам с собой»: sender и receiver живут в главном
            # потоке, поэтому emit из фонового потока ставится в очередь.
            self.deliver.connect(self._on_deliver)

        @pyqtSlot()
        def _on_deliver(self):
            self._bus._drain_pending()

else:  # pragma: no cover - среда без PyQt
    _EventRelay = None


class EventBus:
    """События-факты и перехватываемые UI-действия.

    ``subscribe`` принимает приоритет: чем выше, тем раньше вызывается
    обработчик. Для ``dispatch`` первый обработчик, вернувший truthy,
    останавливает цепочку. Для ``publish`` вызываются все подписчики.

    Фоновая публикация складывается в ограниченную очередь и будит UI
    одним сигналом на пачку, а не сигналом на каждого подписчика.
    """

    def __init__(self, max_pending=4096):
        self._subs = {}
        self._lock = threading.RLock()
        self._order = 0
        self._relay = _EventRelay(self) if _EventRelay is not None else None

        # Очередь фоновой доставки. Один queued-сигнал будит главный поток,
        # дальше он разбирает пачку целиком — вместо сигнала на каждого
        # подписчика. max_pending ограничивает рост очереди.
        self._pending = deque()
        self._pending_lock = threading.RLock()
        self._delivery_scheduled = False
        self.max_pending = max(0, int(max_pending or 0))
        self.dropped_events = 0

    def subscribe(self, name, fn, priority=0, owner=PLATFORM_OWNER):
        """Подписаться на событие.

        ``owner`` — модуль-владелец. Платформенные подписки используют
        ``PLATFORM_OWNER``; модули получают своё имя через SDK-адаптер.
        """
        with self._lock:
            self._order += 1
            entry = _Entry(priority, self._order, fn, owner)
            self._subs.setdefault(name, []).append(entry)
        return Subscription(self, name, entry)

    def unsubscribe(self, name, fn=None, owner=PLATFORM_OWNER):
        """Удалить подписки указанного владельца.

        ``owner=None`` — внутренняя очистка без фильтра. Модуль через
        ``ctx.events.unsubscribe`` всегда передаёт свой owner и не может
        снять чужую подписку.
        """
        wanted = None if owner is None else str(owner)
        with self._lock:
            entries = self._subs.get(name)
            if not entries:
                return 0

            kept = []
            removed = 0
            for entry in entries:
                owner_matches = (
                    wanted is None or entry.owner == wanted
                )
                fn_matches = fn is None or entry.fn is fn
                if owner_matches and fn_matches:
                    entry.active = False
                    removed += 1
                else:
                    kept.append(entry)

            if kept:
                self._subs[name] = kept
            else:
                self._subs.pop(name, None)
            return removed

    def clear(self, name=None, owner=None):
        """Очистить подписки (внутренняя операция платформы).

        ``owner=None`` — все подписки; иначе только указанного владельца.
        """
        with self._lock:
            wanted = None if owner is None else str(owner)
            if name is None:
                groups = list(self._subs.items())
            else:
                groups = [(name, self._subs.get(name, ()))]

            entries = []
            for event_name, items in groups:
                kept = []
                for entry in items:
                    if wanted is None or entry.owner == wanted:
                        entry.active = False
                        entries.append(entry)
                    else:
                        kept.append(entry)
                if kept:
                    self._subs[event_name] = kept
                else:
                    self._subs.pop(event_name, None)
            return len(entries)

    def has(self, name, owner=None):
        with self._lock:
            entries = self._subs.get(name, ())
            if owner is None:
                return bool(entries)
            wanted = str(owner)
            return any(entry.owner == wanted for entry in entries)

    def _remove_entry(self, name, entry):
        with self._lock:
            entry.active = False
            entries = self._subs.get(name)
            if not entries:
                return
            kept = [e for e in entries if e is not entry]
            if kept:
                self._subs[name] = kept
            else:
                self._subs.pop(name, None)

    def _snapshot(self, name):
        with self._lock:
            entries = list(self._subs.get(name, ()))
        # priority: больше — раньше; order: стабильный порядок внутри приоритета.
        entries.sort(key=lambda e: (-e.priority, e.order))
        return entries

    def publish(self, name, payload=None):
        """Опубликовать факт. Вызываются все подписчики."""
        entries = [
            entry for entry in self._snapshot(name)
            if entry.active
        ]
        if not entries:
            return

        if self._needs_queued_delivery():
            self._enqueue(entries, name, payload)
            return

        for entry in entries:
            self._call(entry, name, payload)

    def _enqueue(self, entries, name, payload):
        schedule = False
        with self._pending_lock:
            for entry in entries:
                if (
                    self.max_pending
                    and len(self._pending) >= self.max_pending
                ):
                    self._pending.popleft()
                    self.dropped_events += 1
                    if (
                        self.dropped_events == 1
                        or self.dropped_events % 1000 == 0
                    ):
                        log.warning(
                            "EventBus: очередь переполнена, "
                            "отброшено событий: %d",
                            self.dropped_events,
                        )
                self._pending.append((entry, name, payload))
            if not self._delivery_scheduled:
                self._delivery_scheduled = True
                schedule = True
        if schedule and self._relay is not None:
            self._relay.deliver.emit()

    def _drain_pending(self):
        while True:
            with self._pending_lock:
                if not self._pending:
                    self._delivery_scheduled = False
                    return
                batch = list(self._pending)
                self._pending.clear()

            for entry, name, payload in batch:
                if not entry.active:
                    continue
                self._call(entry, name, payload)

    @property
    def pending_count(self):
        with self._pending_lock:
            return len(self._pending)

    def clear_pending(self):
        with self._pending_lock:
            dropped = len(self._pending)
            self._pending.clear()
            self._delivery_scheduled = False
        return dropped

    def dispatch(self, name, payload=None):
        """Разослать перехватываемое UI-действие.

        Возвращает ``True``, если обработчик перехватил действие.

        ``dispatch`` синхронный и обязан выполняться в UI-потоке: его
        обработчики возвращают результат перехвата и обычно трогают
        виджеты. Из фонового потока используйте ``publish`` или
        отложенный вызов в UI-поток.
        """
        if not self._is_ui_thread():
            log.error(
                "dispatch(%s) вызван не из UI-потока; действие отклонено",
                name,
            )
            return False
        for entry in self._snapshot(name):
            if self._call(entry, name, payload):
                return True
        return False

    @staticmethod
    def _is_ui_thread():
        if QCoreApplication is None:
            return True
        app = QCoreApplication.instance()
        if app is None:
            return True
        return QThread.currentThread() == app.thread()

    def _call(self, entry, name, payload):
        if not entry.active:
            return False
        try:
            return entry.fn(payload)
        except Exception:
            log.error(
                "Ошибка подписчика события %s:\n%s",
                name,
                traceback.format_exc(),
            )
            return False

    def _needs_queued_delivery(self):
        if self._relay is None or QCoreApplication is None:
            return False
        app = QCoreApplication.instance()
        if app is None:
            return False
        return QThread.currentThread() != app.thread()


class CommandError(Exception):
    pass


class CommandBus:
    """Команда — это намерение что-то сделать."""

    def __init__(self):
        self._handlers = {}
        self._lock = threading.RLock()

    def register(self, name, handler, owner=None, replace=False):
        with self._lock:
            existing = self._handlers.get(name)
            if existing is not None and not replace:
                existing_owner = existing[1]
                if owner is not None and existing_owner != owner:
                    raise CommandError(
                        "Команда уже зарегистрирована другим владельцем: %s"
                        % name
                    )
                if owner is None and existing_owner is not None:
                    raise CommandError(
                        "Команда принадлежит модулю: %s" % name
                    )
                log.warning("Команда перерегистрирована: %s", name)
            self._handlers[name] = (handler, owner)

    def unregister(self, name, owner=None):
        with self._lock:
            existing = self._handlers.get(name)
            if existing is None:
                return False
            if owner is not None and existing[1] != owner:
                return False
            self._handlers.pop(name, None)
            return True

    def has(self, name):
        with self._lock:
            return name in self._handlers

    def execute(self, name, *args, **kwargs):
        with self._lock:
            entry = self._handlers.get(name)
            handler = entry[0] if entry is not None else None
        if handler is None:
            raise CommandError("Команда не зарегистрирована: %s" % name)
        return handler(*args, **kwargs)
