# -*- coding: utf-8 -*-
"""Фоновые задачи платформы (чтобы модули не блокировали UI).

Колбэки ``on_done`` и ``on_log`` всегда вызываются в потоке Qt-приложения.
Это важно: сигналы QThread, подключённые к обычным Python-функциям, в PyQt
выполняются в потоке-отправителе, а не в главном.
"""

from PyQt5.QtCore import QObject, QThread, pyqtSignal, pyqtSlot


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
            self.log.emit(traceback.format_exc())
        self.finished_task.emit(res, err)


class _LogRelay(QObject):
    """Выполняет ``on_log`` в потоке, где создан relay (главном)."""

    def __init__(self, callback, parent=None):
        super().__init__(parent)
        self._callback = callback

    @pyqtSlot(str)
    def deliver(self, message):
        self._callback(message)


class _DoneRelay(QObject):
    """Выполняет ``on_done`` в потоке, где создан relay (главном)."""

    def __init__(self, callback, parent=None):
        super().__init__(parent)
        self._callback = callback

    @pyqtSlot(object, object)
    def deliver(self, result, error):
        self._callback(result, error)


class _CleanupRelay(QObject):
    def __init__(self, service, worker, parent=None):
        super().__init__(parent)
        self._service = service
        self._worker = worker

    @pyqtSlot()
    def deliver(self):
        self._service._cleanup(self._worker)


class TaskService:
    def __init__(self):
        self._workers = set()

    def run(self, fn, on_done=None, on_log=None):
        w = _Worker(fn)

        if on_log is not None:
            log_relay = _LogRelay(on_log, parent=w)
            w.log.connect(log_relay.deliver)

        if on_done is not None:
            done_relay = _DoneRelay(on_done, parent=w)
            w.finished_task.connect(done_relay.deliver)

        cleanup = _CleanupRelay(self, w, parent=w)
        w.finished.connect(cleanup.deliver)

        self._workers.add(w)
        w.start()
        return w

    def _cleanup(self, w):
        self._workers.discard(w)
