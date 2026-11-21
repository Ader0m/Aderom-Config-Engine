# -*- coding: utf-8 -*-
"""Владение регистрациями модулей.

Модуль регистрирует в платформе меню, действия, обработчики, настройки
и другие возможности. Каждая такая регистрация возвращает
:class:`RegistrationHandle`: пока handle жив, регистрация действует;
``handle.unsubscribe()`` снимает её.

:class:`RegistrationScope` собирает все handle'ы одного модуля. Загрузчик
закрывает scope при ``deactivate``, поэтому даже неполный ``deactivate``
модуля не оставляет «висячих» меню, горячих клавиш и обработчиков.

Модуль не должен импортировать ``ui``/``core``: здесь только стандартная
библиотека.
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)


def remove_identity(items, item):
    """Удалить элемент списка по идентичности, а не по равенству.

    ``list.remove`` сравнивает значения: две одинаковые регистрации от
    разных модулей могли бы удалить не ту запись. Здесь сравнивается
    именно объект.
    """
    for index, candidate in enumerate(items):
        if candidate is item:
            del items[index]
            return True
    return False


class RegistrationHandle:
    """Право снять одну регистрацию.

    Повторный ``unsubscribe`` безопасен: возвращает ``False`` и ничего не
    делает. Handle принадлежит только тому, кто его получил, поэтому
    модуль не может снять чужую регистрацию.
    """

    __slots__ = ("_dispose", "_active", "owner")

    def __init__(self, dispose, owner=""):
        self._dispose = dispose
        self._active = True
        self.owner = str(owner or "")

    @property
    def active(self):
        return self._active

    def unsubscribe(self):
        if not self._active:
            return False
        self._active = False
        self._dispose()
        return True

    # Совместимое имя: часть подписок SDK уже использует ``dispose``.
    dispose = unsubscribe


class RegistrationScope:
    """Все регистрации одного модуля.

    ``owner`` — имя модуля из ``manifest.name``. Scope создаёт
    ``ModuleContext``; загрузчик вызывает :meth:`dispose` после
    ``module.deactivate()``.
    """

    def __init__(self, owner=""):
        self.owner = str(owner or "")
        self._handles = []
        self._lock = threading.RLock()
        self._closed = False

    @property
    def closed(self):
        return self._closed

    def __len__(self):
        with self._lock:
            return len(self._handles)

    def track(self, handle):
        """Взять регистрацию под управление scope.

        ``None`` допустим: не все платформенные методы возвращают
        handle (например, старые тестовые двойники шин). Повторная
        регистрация после ``dispose`` сразу отменяется, чтобы модуль,
        который ещё работает после выгрузки, не оставлял следов.
        """
        if handle is None:
            return None
        if not hasattr(handle, "unsubscribe"):
            raise TypeError(
                "Регистрация должна иметь unsubscribe(): %r"
                % (handle,)
            )

        with self._lock:
            if self._closed:
                try:
                    handle.unsubscribe()
                except Exception:
                    log.exception(
                        "Модуль %s: поздняя регистрация не снята",
                        self.owner,
                    )
                raise RuntimeError(
                    "Модуль %s уже выгружен: регистрация отклонена"
                    % (self.owner or "<unknown>")
                )
            self._handles.append(handle)
        return handle

    def track_callable(self, dispose, owner=None):
        """Обернуть функцию очистки в handle и взять её в scope."""
        return self.track(
            RegistrationHandle(
                dispose,
                self.owner if owner is None else owner,
            )
        )

    def dispose(self):
        """Снять все регистрации в обратном порядке.

        Ошибка одной регистрации не мешает снять остальные. Возвращает
        количество попыток снятия (не считая уже снятых handle'ов).
        """
        with self._lock:
            if self._closed:
                return 0
            self._closed = True
            handles = list(reversed(self._handles))
            self._handles.clear()

        for handle in handles:
            try:
                handle.unsubscribe()
            except Exception:
                log.exception(
                    "Модуль %s: ошибка снятия регистрации",
                    self.owner,
                )
        return len(handles)
