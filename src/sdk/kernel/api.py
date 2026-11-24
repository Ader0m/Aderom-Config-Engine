# -*- coding: utf-8 -*-
"""Контракты задач, событий и команд."""

from __future__ import annotations

from typing import Callable, Optional, Protocol


class TaskApi(Protocol):
    """API фоновых задач, доступное модулю через ``ctx.tasks``."""

    def run(
        self,
        fn: Callable,
        on_done: Optional[Callable] = None,
        on_log: Optional[Callable] = None,
        operation_key: Optional[str] = None,
        title: Optional[str] = None,
        depends_on=(),
        priority: int = 0,
    ):
        """Запустить ``fn(log)`` в фоне.

        ``fn`` выполняется в рабочем потоке. ``on_done`` и ``on_log``
        вызываются в потоке UI — в них можно обращаться к виджетам.

        ``operation_key`` — стабильный ключ операции. Повторный запуск с
        тем же ключом во время активной операции не создаёт дубль, а
        возвращает существующий handle и подписывает переданные
        ``on_done``/``on_log`` на эту операцию. Ключи модуля
        нормализуются по owner; глобальные — `vfs.*`, `project.*`,
        `platform.*`. ``depends_on`` — ключи операций или callable,
        возвращающий их; callable вычисляется атомарно при постановке
        задачи в очередь. ``priority`` — очередь пула: большее значение
        запускается раньше.
        """

    def operation_active(self, operation_key: str) -> bool:
        """Активна ли операция с указанным ключом (queued/running)."""

class EventApi(Protocol):
    """Шина событий, доступная модулю через ``ctx.events``.

    Имена событий следуют политике ``<owner>.<name>``: ``app.*``/``ui.*``
    принадлежат платформе, ``<module>.*`` — модулю. ``publish`` уведомляет
    о факте и вызывает всех подписчиков. ``dispatch`` рассылает
    перехватываемое UI-действие: первый обработчик, вернувший truthy,
    останавливает цепочку.

    ``publish`` можно вызывать из фонового потока: подписчики получат
    событие в потоке UI. ``dispatch`` предназначен для вызова из UI-потока.
    """

    def subscribe(self, name: str, handler: Callable, priority: int = 0):
        """Подписаться. Возвращает объект с методом ``unsubscribe()``.

        Подписка принадлежит текущему модулю: снять её можно только своим
        handle или ``unsubscribe`` с тем же owner.
        """

    def unsubscribe(self, name: str, handler: Optional[Callable] = None) -> int:
        """Отписаться от своих подписок события.

        Без ``handler`` снимаются все подписки **текущего модуля** на это
        событие. Чужие подписки (платформы и других модулей) не
        затрагиваются.
        """

    def publish(self, name: str, payload=None) -> None:
        """Опубликовать факт."""

    def dispatch(self, name: str, payload=None) -> bool:
        """Разослать перехватываемое действие."""

    def has(self, name: str) -> bool:
        """Есть ли подписчики у события."""

class CommandApi(Protocol):
    """Шина команд, доступная модулю через ``ctx.commands``."""

    def register(self, name: str, handler: Callable) -> None:
        """Зарегистрировать команду."""

    def unregister(self, name: str) -> bool:
        """Удалить команду."""

    def has(self, name: str) -> bool:
        """Зарегистрирована ли команда."""

    def execute(self, name: str, *args, **kwargs):
        """Выполнить команду."""
