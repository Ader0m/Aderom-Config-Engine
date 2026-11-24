# -*- coding: utf-8 -*-
"""Фоновые задачи платформы и общий реестр операций.

Колбэки ``on_done`` и ``on_log`` всегда вызываются в потоке Qt-приложения.
Это важно: сигналы QThread, подключённые к обычным Python-функциям, в PyQt
выполняются в потоке-отправителе, а не в главном.

``TaskService`` остаётся владельцем выполнения, а ``OperationRegistry``
хранит состояния ``queued/running/done/failed/cancelled``, прогресс,
владельца и зависимости. Одинаковый ``operation_key`` при активной
операции не создаёт вторую работу.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import traceback

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

from .operations import (
    ACTIVE_STATES,
    CANCELLED,
    DONE,
    FAILED,
    FINAL_STATES,
    QUEUED,
    RUNNING,
    OperationRegistry,
    OperationSnapshot,
    normalize_operation_key,
)

log = logging.getLogger(__name__)

_CURRENT_TASK = threading.local()


def current_task():
    """Задача, выполняемая в текущем рабочем потоке (или ``None``)."""
    return getattr(_CURRENT_TASK, "handle", None)


def _safe_call(label, callback, *args):
    """Вызвать callback из Qt-слота, не выпуская исключение в event loop."""
    try:
        return callback(*args)
    except Exception:
        log.exception("TaskService: ошибка %s", label)
        return None


def _remaining_ms(deadline):
    """Остаток общего deadline в миллисекундах (не меньше нуля)."""
    return max(0, int((deadline - time.monotonic()) * 1000))


class _TaskRelay(QObject):
    """Доставляет лог и завершение задачи в поток, где создан relay."""

    log = pyqtSignal(str)
    finished = pyqtSignal(object, object)  # (result, error)

    def __init__(self, service, handle, parent=None):
        super().__init__(parent)
        self._service = service
        self._handle = handle
        self.log.connect(self._deliver_log)
        self.finished.connect(self._deliver_finished)

    @pyqtSlot(str)
    def _deliver_log(self, message):
        self._service._on_log(self._handle, message)

    @pyqtSlot(object, object)
    def _deliver_finished(self, result, error):
        self._service._finish_handle(self._handle, result, error)


class _PumpRelay(QObject):
    """Гарантирует, что пробуждение зависимых задач идёт из UI-потока."""

    pump = pyqtSignal()

    def __init__(self, callback, parent=None):
        super().__init__(parent)
        self._callback = callback
        self.pump.connect(self._deliver)

    @pyqtSlot()
    def _deliver(self):
        _safe_call("pump_pending", self._callback)


class TaskHandle:
    """Пользовательский handle операции: прогресс, отмена и ожидание."""

    def __init__(
        self,
        service,
        key,
        owner,
        title,
        fn,
        on_done=None,
        on_log=None,
        depends_on=(),
        priority=0,
    ):
        self.service = service
        self.key = str(key)
        self.owner = str(owner or "")
        self.title = str(title or self.key)
        self.fn = fn
        self.on_done = on_done
        self.on_log = on_log
        self.dependencies = tuple(str(item) for item in depends_on or ())
        self.priority = int(priority or 0)
        self._done_callbacks = []
        self._log_callbacks = []
        if callable(on_done):
            self._done_callbacks.append(on_done)
        if callable(on_log):
            self._log_callbacks.append(on_log)

        self.state = QUEUED
        self.done = 0
        self.total = 0
        self.message = ""
        self.error = ""
        self.result = None
        self.duplicate_count = 0
        self.started_at = 0.0
        self.finished_at = 0.0
        self.relay = None
        self._worker_result = None
        self._worker_error = None

        self._cancel = threading.Event()
        self._done = threading.Event()
        self._worker_done = threading.Event()

    @property
    def is_active(self):
        return self.state in ACTIVE_STATES

    @property
    def is_cancelled(self):
        return self._cancel.is_set()

    @property
    def is_finished(self):
        """Выполнены ли работа, ``on_done`` и финальное состояние."""
        return self._done.is_set()

    def snapshot(self):
        return OperationSnapshot(
            key=self.key,
            owner=self.owner,
            title=self.title,
            state=self.state,
            done=int(self.done or 0),
            total=int(self.total or 0),
            message=str(self.message or ""),
            error=str(self.error or ""),
            dependencies=self.dependencies,
            duplicate_count=int(self.duplicate_count or 0),
            started_at=float(self.started_at or 0.0),
            finished_at=float(self.finished_at or 0.0),
        )

    def add_subscriber(self, on_done=None, on_log=None):
        """Подписать ещё одного клиента на существующую операцию."""
        if callable(on_done) and on_done not in self._done_callbacks:
            self._done_callbacks.append(on_done)
        if callable(on_log) and on_log not in self._log_callbacks:
            self._log_callbacks.append(on_log)

    def done_callbacks(self):
        return tuple(self._done_callbacks)

    def log_callbacks(self):
        return tuple(self._log_callbacks)

    def publish(self, notify=True):
        if self.state in FINAL_STATES:
            self.service.operations.finish(
                self.snapshot(),
                notify=notify,
            )
        else:
            self.service.operations.update(self.snapshot())

    def progress(self, done=None, total=None, message=None):
        """Обновить прогресс. Безопасно вызывать из рабочего потока."""
        if done is not None:
            self.done = max(0, int(done))
        if total is not None:
            self.total = max(0, int(total))
        if message is not None:
            self.message = str(message)
        self.publish()

    def set_message(self, message):
        self.message = str(message or "")
        self.publish()

    def cancel(self):
        """Попросить операцию остановиться (кооперативно)."""
        self._cancel.set()
        if self.state == QUEUED:
            self.service._cancel_queued(self)

    # Совместимость с прежним QThread-подобным результатом run().
    def requestInterruption(self):
        self.cancel()

    def isInterruptionRequested(self):
        return self._cancel.is_set()

    def wait(self, timeout_ms=5000):
        """Дождаться финального состояния и ``on_done``.

        Не вызывать из UI-потока: callbacks доставляются в UI-поток.
        """
        timeout = max(0.0, float(timeout_ms or 0) / 1000.0)
        return self._done.wait(timeout)

    def _wait_work(self, timeout_ms=5000):
        """Дождаться только завершения ``fn`` (для shutdown)."""
        timeout = max(0.0, float(timeout_ms or 0) / 1000.0)
        if self._done.is_set() or self._worker_done.is_set():
            return True
        return self._worker_done.wait(timeout)


class TaskService:
    def __init__(self, operations=None, max_workers=4):
        self.operations = operations or OperationRegistry()
        self.max_workers = max(1, int(max_workers or 1))
        self._lock = threading.RLock()
        self._handles = {}
        self._pending = []
        self._workers = set()
        self._owners = {}
        self._queue = queue.PriorityQueue()
        self._queue_seq = 0
        self._pool = []
        self._stop_workers = threading.Event()
        self._seq = 0
        self._shutdown = False
        self.operations.add_finished_listener(
            self._on_operation_finished
        )
        self._pump_relay = _PumpRelay(self._pump_pending)

    # -------------------------------------------------- запуск
    def run(
        self,
        fn,
        on_done=None,
        on_log=None,
        owner=None,
        operation_key=None,
        title=None,
        depends_on=(),
        priority=0,
    ):
        """Запустить задачу или вернуть уже активную операцию.

        ``operation_key`` — стабильный ключ сценария. Одинаковый ключ при
        активной операции не создаёт второй задачи: возвращается
        существующий ``TaskHandle``, а ``duplicate_count`` растёт.
        ``depends_on`` — ключи операций, после завершения которых задачу
        можно начинать, либо callable, возвращающий их; callable
        вычисляется под lock TaskService, поэтому старт `vfs.rebuild`
        не теряется между проверкой и постановкой в очередь.
        Неизвестный ключ — ошибка.
        ``priority`` — очередь пула: большее значение запускается раньше.
        """
        owner = str(owner or "")
        with self._lock:
            if self._shutdown:
                raise RuntimeError("TaskService остановлен")
            key = self._make_key(owner, operation_key)
            raw_dependencies = (
                depends_on() if callable(depends_on) else depends_on
            )
            dependencies = self._make_dependencies(owner, raw_dependencies)
            existing = self._handles.get(key)
            if existing is not None and existing.is_active:
                existing.add_subscriber(on_done, on_log)
                existing.duplicate_count += 1
                existing.publish()
                return existing

            if key in dependencies:
                raise ValueError(
                    "Операция не может зависеть от себя: %s" % key
                )
            missing = [
                dependency
                for dependency in dependencies
                if self.operations.get(dependency) is None
                and not self.operations.has_seen(dependency)
            ]
            if missing:
                raise ValueError(
                    "Неизвестная зависимость операции %s: %s"
                    % (key, ", ".join(missing))
                )
            if self._dependency_cycle(key, dependencies):
                raise ValueError(
                    "Циклическая зависимость операции: %s" % key
                )

            handle = TaskHandle(
                self,
                key,
                owner,
                title or key,
                fn,
                on_done=on_done,
                on_log=on_log,
                depends_on=dependencies,
                priority=priority,
            )
            handle.relay = _TaskRelay(self, handle)
            self._handles[key] = handle
            self.operations.register(
                handle.snapshot(),
                cancel=handle.cancel,
            )
            self._ensure_pool_locked()

            # Проверка зависимостей, постановка в pending и scheduling
            # выполняются под общим lock. Иначе завершение зависимости
            # между проверкой и append/pump могло потерять пробуждение.
            problem = self._dependency_problem(handle)
            blocked = False
            if problem is None:
                blocked = self._dependencies_blocked(handle)
                if blocked:
                    handle.message = self._waiting_message(handle)
                    self._pending.append(handle)
                else:
                    self._schedule(handle)

        if problem is not None:
            self._finish_handle(
                handle,
                None,
                RuntimeError(problem),
                pump=False,
            )
            return handle

        if blocked:
            handle.publish()
            return handle

        return handle

    def _make_key(self, owner, operation_key):
        if operation_key:
            return normalize_operation_key(owner, operation_key)
        self._seq += 1
        return "%s:task-%d" % (owner or "<platform>", self._seq)

    def _make_dependencies(self, owner, depends_on):
        result = []
        for item in depends_on or ():
            key = normalize_operation_key(owner, item)
            if key and key not in result:
                result.append(key)
        return tuple(result)

    def _dependency_cycle(self, key, dependencies):
        """Проверить цикл по уже известным операциям и новой зависимости."""
        graph = {
            handle.key: handle.dependencies
            for handle in self._handles.values()
        }
        graph[key] = tuple(dependencies or ())
        visiting = set()
        visited = set()

        def visit(node):
            if node in visiting:
                return True
            if node in visited:
                return False
            visiting.add(node)
            for dependency in graph.get(node, ()):
                if visit(dependency):
                    return True
            visiting.remove(node)
            visited.add(node)
            return False

        return visit(key)

    def _ensure_pool_locked(self):
        """Запустить недостающие daemon-потоки пула (под self._lock)."""
        if self._shutdown:
            return
        self._pool = [thread for thread in self._pool if thread.is_alive()]
        while len(self._pool) < self.max_workers:
            index = len(self._pool) + 1
            thread = threading.Thread(
                target=self._worker_loop,
                name="ace-task-%d" % index,
                daemon=True,
            )
            self._pool.append(thread)
            thread.start()

    def _worker_loop(self):
        while not self._stop_workers.is_set():
            try:
                _priority, _seq, handle = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                if self._stop_workers.is_set():
                    break
                self._execute(handle)
            except Exception:
                log.exception("TaskService: ошибка рабочего потока")
            finally:
                self._queue.task_done()

    def _execute(self, handle):
        with self._lock:
            if handle.state in FINAL_STATES:
                return
            handle.state = RUNNING
            handle.started_at = time.time()
            handle.message = ""
            self._workers.add(handle)
            self._owners[handle] = handle.owner
        handle.publish()

        relay = handle.relay
        _CURRENT_TASK.handle = handle
        result = None
        error = None
        try:
            result = handle.fn(
                lambda message: relay.log.emit(str(message))
            )
        except Exception as exc:
            error = exc
            try:
                relay.log.emit(traceback.format_exc())
            except Exception:
                pass
        finally:
            _CURRENT_TASK.handle = None
            handle._worker_result = result
            handle._worker_error = error
            handle._worker_done.set()
            relay.finished.emit(result, error)

    def _schedule(self, handle):
        """Поставить задачу в приоритетную очередь пула."""
        with self._lock:
            if self._shutdown or handle.state in FINAL_STATES:
                return
            self._queue_seq += 1
            self._queue.put(
                (
                    -int(handle.priority or 0),
                    self._queue_seq,
                    handle,
                )
            )

    def _on_log(self, handle, message):
        handle.set_message(message)
        for callback in handle.log_callbacks():
            _safe_call("on_log", callback, message)

    # -------------------------------------------------- завершение
    def _finish_handle(self, handle, result, error, pump=True):
        with self._lock:
            if handle.state in FINAL_STATES:
                return
            if handle.is_cancelled:
                state = CANCELLED
                result = None
                if error is None:
                    error = RuntimeError("Операция отменена")
            elif error is not None:
                state = FAILED
                result = None
            else:
                state = DONE

            handle.state = state
            handle.result = result
            handle.error = "" if error is None else str(error)
            handle.finished_at = time.time()
            if handle in self._pending:
                self._pending.remove(handle)
            self._workers.discard(handle)
            self._owners.pop(handle, None)
            if self._handles.get(handle.key) is handle:
                self._handles.pop(handle.key, None)
            relay = handle.relay
            handle.relay = None

        try:
            if relay is not None:
                relay.deleteLater()
            handle.publish(notify=False)

            for callback in handle.done_callbacks():
                _safe_call("on_done", callback, result, error)

            # Зависимые задачи стартуют после on_done: их side effects уже
            # видны, а состояние операции остаётся завершённым.
            self.operations.notify_finished(handle.snapshot())

            if pump:
                self._pump_pending()
        finally:
            # wait()/wait_owner() должны видеть уже выполненные callbacks,
            # а не только завершившийся рабочий поток.
            handle._done.set()

    def _cancel_queued(self, handle):
        with self._lock:
            if handle.state != QUEUED:
                return
            if handle in self._pending:
                self._pending.remove(handle)
        self._finish_handle(handle, None, None)

    def _on_operation_finished(self, _snapshot):
        """Разбудить зависимые задачи после внешней/своей операции.

        LISTENER может быть вызван из рабочего потока. ``_PumpRelay``
        доставляет пробуждение в UI-поток, поэтому ``on_done`` и
        постановка задач не выполняются в чужом потоке.
        """
        self._pump_relay.pump.emit()

    def _pump_pending(self):
        if self._shutdown:
            return
        problems = []
        deferred = []
        with self._lock:
            pending, self._pending = self._pending, []
            for handle in pending:
                problem = self._dependency_problem(handle)
                if problem is not None:
                    problems.append((handle, problem))
                    continue
                if self._dependencies_blocked(handle):
                    handle.message = self._waiting_message(handle)
                    deferred.append(handle)
                    continue
                self._schedule(handle)
            if deferred:
                self._pending.extend(deferred)

        for handle in deferred:
            handle.publish()
        for handle, problem in problems:
            self._finish_handle(
                handle,
                None,
                RuntimeError(problem),
                pump=False,
            )

    # -------------------------------------------------- зависимости
    def _dependency_problem(self, handle):
        for key in handle.dependencies:
            snapshot = self.operations.get(key)
            state = (
                snapshot.state
                if snapshot is not None
                else self.operations.last_state(key)
            )
            if state in (FAILED, CANCELLED):
                return "Зависимость не выполнена: %s" % key
        return None

    def _dependencies_blocked(self, handle):
        for key in handle.dependencies:
            snapshot = self.operations.get(key)
            if snapshot is not None and snapshot.is_active:
                return True
        return False

    @staticmethod
    def _waiting_message(handle):
        active = [
            key
            for key in handle.dependencies
            if key
        ]
        if active:
            return "ожидает: %s" % ", ".join(active)
        return "в очереди"

    # -------------------------------------------------- отмена / ожидание
    def cancel_operation(self, key):
        handle = self._handles.get(str(key or ""))
        if handle is None:
            return False
        handle.cancel()
        return True

    def workers_for(self, owner):
        wanted = str(owner or "")
        with self._lock:
            return tuple(
                handle
                for handle in self._workers
                if self._owners.get(handle) == wanted
            )

    def cancel_owner(self, owner):
        """Попросить задачи владельца завершиться (кооперативно)."""
        wanted = str(owner or "")
        with self._lock:
            handles = tuple(
                handle
                for handle in self._handles.values()
                if handle.owner == wanted and handle.is_active
            )
        for handle in handles:
            try:
                handle.cancel()
            except RuntimeError:
                pass
        return len(handles)

    def wait_owner(self, owner, timeout_ms=5000):
        """Дождаться задач владельца. ``False`` — не все успели завершиться.

        Не вызывать из UI-потока: ``on_done`` доставляются в UI-поток,
        поэтому ожидание из UI не даст callbacks выполниться до timeout.
        Для выгрузки модуля используйте ``cancel_owner`` и неблокирующий
        cleanup; для фонового ожидания — этот метод.
        """
        deadline = time.monotonic() + max(0, timeout_ms) / 1000.0
        wanted = str(owner or "")
        with self._lock:
            handles = tuple(
                handle
                for handle in self._handles.values()
                if handle.owner == wanted
            )
        finished = True
        for handle in handles:
            if not handle.wait(_remaining_ms(deadline)):
                finished = False
        return finished

    def wait_all(self, timeout_ms=5000):
        """Дождаться всех задач и их callbacks.

        Не вызывать из UI-потока: ``on_done`` доставляются в UI-поток,
        поэтому ожидание из UI не даст callbacks выполниться до timeout.
        Для shutdown используйте внутренний work-wait путь.
        """
        deadline = time.monotonic() + max(0, timeout_ms) / 1000.0
        with self._lock:
            handles = tuple(self._handles.values())
        finished = True
        for handle in handles:
            if not handle.wait(_remaining_ms(deadline)):
                finished = False
        return finished

    def _wait_all_work(self, timeout_ms=5000):
        """Дождаться только рабочих потоков, без UI-callbacks (shutdown)."""
        deadline = time.monotonic() + max(0, timeout_ms) / 1000.0
        with self._lock:
            handles = tuple(self._handles.values())
        finished = True
        for handle in handles:
            if not handle._wait_work(_remaining_ms(deadline)):
                finished = False
        return finished

    def _finalize_worker_done(self):
        """Завершить задачи, чьи рабочие потоки уже закончили.

        Нужно для shutdown после остановки event loop: relay-сигнал может
        уже не дойти, поэтому callbacks выполняются здесь, в UI-потоке.
        """
        with self._lock:
            ready = [
                handle
                for handle in self._handles.values()
                if handle._worker_done.is_set()
                and handle.state not in FINAL_STATES
            ]
        for handle in ready:
            self._finish_handle(
                handle,
                handle._worker_result,
                handle._worker_error,
            )

    def shutdown(self, timeout_ms=5000):
        """Остановить приём задач, отменить активные и погасить пул."""
        deadline = time.monotonic() + max(0, timeout_ms) / 1000.0
        with self._lock:
            self._shutdown = True
            self._stop_workers.set()
            pool = tuple(self._pool)
            self._pool = []
        self.cancel_all()
        finished = self._wait_all_work(_remaining_ms(deadline))
        self._finalize_worker_done()
        for thread in pool:
            remaining = max(0.0, deadline - time.monotonic())
            thread.join(remaining)
        return finished and not any(thread.is_alive() for thread in pool)

    def cancel_all(self):
        with self._lock:
            handles = tuple(self._handles.values())
        for handle in handles:
            try:
                handle.cancel()
            except RuntimeError:
                pass
        return len(handles)
