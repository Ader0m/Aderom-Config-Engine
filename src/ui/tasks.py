# -*- coding: utf-8 -*-
"""Фоновые задачи платформы (чтобы модули не блокировали UI)."""

from PyQt5.QtCore import QThread, pyqtSignal


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


class TaskService:
    def __init__(self):
        self._workers = set()

    def run(self, fn, on_done=None, on_log=None):
        w = _Worker(fn)

        if on_log is not None:
            w.log.connect(on_log)
        if on_done is not None:
            w.finished_task.connect(on_done)

        w.finished_task.connect(lambda *_: self._cleanup(w))

        self._workers.add(w)
        w.start()
        return w

    def _cleanup(self, w):
        self._workers.discard(w)