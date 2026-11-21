# -*- coding: utf-8 -*-
"""Фоновые задачи платформы (чтобы модули не блокировали UI).

Колбэки ``on_done`` и ``on_log`` всегда вызываются в потоке Qt-приложения.
Это важно: сигналы QThread, подключённые к обычным Python-функциям, в PyQt
выполняются в потоке-отправителе, а не в главном.
"""

import logging

from PyQt5.QtCore import QObject, QThread, pyqtSignal, pyqtSlot

log = logging.getLogger(__name__)


def _safe_call(label, callback, *args):
    """Вызвать callback из Qt-слота, не выпуская исключение в event loop."""
    try:
        return callback(*args)
    except Exception:
        log.exception("TaskService: ошибка %s", label)
        return None


class _Worker(QThread):
    log = pyqtSignal(str)
    finished_task = pyqtSignal(object, object)  # (result, error)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        import traceback

        res, err = None, None
        try:
            res = self.fn(self.log.emit)
        except Exception as e:
            err = e
            try:
                self.log.emit(traceback.format_exc())
            except Exception:
                pass
        finally:
            self.finished_task.emit(res, err)


class _LogRelay(QObject):
    """Выполняет ``on_log`` в потоке, где создан relay (главном)."""

    def __init__(self, callback, parent=None):
        super().__init__(parent)
        self._callback = callback

    @pyqtSlot(str)
    def deliver(self, message):
        _safe_call("on_log", self._callback, message)


class _DoneRelay(QObject):
    """Выполняет ``on_done`` в потоке, где создан relay (главном)."""

    def __init__(self, callback, parent=None):
        super().__init__(parent)
        self._callback = callback

    @pyqtSlot(object, object)
    def deliver(self, result, error):
        _safe_call("on_done", self._callback, result, error)


class _CleanupRelay(QObject):
    def __init__(self, service, worker, parent=None):
        super().__init__(parent)
        self._service = service
        self._worker = worker

    @pyqtSlot()
    def deliver(self):
        _safe_call("cleanup", self._service._cleanup, self._worker)


class TaskService:
    def __init__(self):
        self._workers = set()
        self._owners = {}

    def run(self, fn, on_done=None, on_log=None, owner=None):
        w = _Worker(fn)

        if on_log is not None and callable(on_log):
            log_relay = _LogRelay(on_log, parent=w)
            w.log.connect(log_relay.deliver)

        if on_done is not None and callable(on_done):
            done_relay = _DoneRelay(on_done, parent=w)
            w.finished_task.connect(done_relay.deliver)

        cleanup = _CleanupRelay(self, w, parent=w)
        w.finished.connect(cleanup.deliver)

        self._workers.add(w)
        self._owners[w] = str(owner or "")
        w.start()
        return w

    def _cleanup(self, w):
        self._workers.discard(w)
        self._owners.pop(w, None)

    def workers_for(self, owner):
        wanted = str(owner or "")
        return tuple(
            worker
            for worker in self._workers
            if self._owners.get(worker) == wanted
        )

    def cancel_owner(self, owner):
        """Попросить задачи модуля завершиться (кооперативно)."""
        workers = self.workers_for(owner)
        for worker in workers:
            try:
                worker.requestInterruption()
            except RuntimeError:
                pass
        return len(workers)

    def wait_owner(self, owner, timeout_ms=5000):
        """Дождаться задач модуля. ``False`` — не все успели завершиться."""
        finished = True
        for worker in self.workers_for(owner):
            if not worker.wait(int(timeout_ms)):
                finished = False
        return finished

    def wait_all(self, timeout_ms=5000):
        finished = True
        for worker in tuple(self._workers):
            if not worker.wait(int(timeout_ms)):
                finished = False
        return finished

    def shutdown(self, timeout_ms=5000):
        """Остановить приём задач и дождаться текущих."""
        self.cancel_all()
        return self.wait_all(timeout_ms=timeout_ms)

    def cancel_all(self):
        workers = tuple(self._workers)
        for worker in workers:
            try:
                worker.requestInterruption()
            except RuntimeError:
                pass
        return len(workers)
