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

try:  # Qt нужен только для доставки событий в главный поток.
    from PyQt5.QtCore import QCoreApplication, QObject, QThread, pyqtSignal, pyqtSlot
except ImportError:  # pragma: no cover - среда без PyQt
    QCoreApplication = None
    QObject = None
    QThread = None
    pyqtSignal = None
    pyqtSlot = None

log = logging.getLogger(__name__)


class Subscription:
    """Подписка на событие. Позволяет отписаться без знания внутренностей шины."""

    def __init__(self, bus, name, entry):
        self._bus = bus
        self._name = name
        self._entry = entry
        self._active = True

    @property
    def active(self):
        return self._active

    def unsubscribe(self):
        """Отписаться. Повторный вызов безопасен."""
        if self._active:
            self._bus._remove_entry(self._name, self._entry)
            self._active = False


class _Entry:
    __slots__ = ("priority", "order", "fn", "active")

    def __init__(self, priority, order, fn):
        self.priority = priority
        self.order = order
        self.fn = fn
        self.active = True


if QObject is not None:

    class _EventRelay(QObject):
        """Переносит вызов подписчика в поток, где создана шина."""

        deliver = pyqtSignal(object, str, object)

        def __init__(self, bus):
            super().__init__()
            self._bus = bus
            # Соединение «сам с собой»: sender и receiver живут в главном
            # потоке, поэтому emit из фонового потока ставится в очередь.
            self.deliver.connect(self._on_deliver)

        @pyqtSlot(object, str, object)
        def _on_deliver(self, entry, name, payload):
            self._bus._call(entry, name, payload)

else:  # pragma: no cover - среда без PyQt
    _EventRelay = None


class EventBus:
    """События-факты и перехватываемые UI-действия.

    ``subscribe`` принимает приоритет: чем выше, тем раньше вызывается
    обработчик. Для ``dispatch`` первый обработчик, вернувший truthy,
    останавливает цепочку. Для ``publish`` вызываются все подписчики.
    """

    def __init__(self):
        self._subs = {}
        self._lock = threading.RLock()
        self._order = 0
        self._relay = _EventRelay(self) if _EventRelay is not None else None

    def subscribe(self, name, fn, priority=0):
        with self._lock:
            self._order += 1
            entry = _Entry(priority, self._order, fn)
            self._subs.setdefault(name, []).append(entry)
        return Subscription(self, name, entry)

    def unsubscribe(self, name, fn=None):
        """Удалить подписку по функции. Без ``fn`` — все подписки события."""
        with self._lock:
            entries = self._subs.get(name)
            if not entries:
                return 0

            if fn is None:
                removed = len(entries)
                self._subs.pop(name, None)
                for entry in entries:
                    entry.active = False
                return removed

            kept = []
            removed = 0
            for entry in entries:
                if entry.fn is fn:
                    entry.active = False
                    removed += 1
                else:
                    kept.append(entry)
            if kept:
                self._subs[name] = kept
            else:
                self._subs.pop(name, None)
            return removed

    def clear(self, name=None):
        with self._lock:
            if name is None:
                entries = [e for group in self._subs.values() for e in group]
                self._subs.clear()
            else:
                entries = self._subs.pop(name, ())
            for entry in entries:
                entry.active = False

    def has(self, name):
        with self._lock:
            return bool(self._subs.get(name))

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
        """Опубликовать факт. Вызываются все подписчики, ошибки не прерывают цепочку."""
        for entry in self._snapshot(name):
            if not entry.active:
                continue
            if self._needs_queued_delivery():
                self._relay.deliver.emit(entry, name, payload)
            else:
                self._call(entry, name, payload)

    def dispatch(self, name, payload=None):
        """Разослать перехватываемое UI-действие.

        Возвращает ``True``, если обработчик перехватил действие.
        """
        for entry in self._snapshot(name):
            if self._call(entry, name, payload):
                return True
        return False

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

    def register(self, name, handler):
        if name in self._handlers:
            log.warning("Команда перерегистрирована: %s", name)
        self._handlers[name] = handler

    def unregister(self, name):
        return self._handlers.pop(name, None) is not None

    def has(self, name):
        return name in self._handlers

    def execute(self, name, *args, **kwargs):
        h = self._handlers.get(name)
        if h is None:
            raise CommandError("Команда не зарегистрирована: %s" % name)
        return h(*args, **kwargs)
