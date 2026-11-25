# -*- coding: utf-8 -*-
"""Фоновая предсборка LTX/XML."""

from __future__ import annotations

import threading
from pathlib import Path

from core.resolver.prebuild import PrebuildService


class PrebuildServices:
    """Состояние и запуск фоновой предсборки."""

    def __init__(self, resolver, xml, cache_service=None, service=None):
        self.prebuild = service or PrebuildService(resolver, xml)
        self.cache_service = cache_service
        self._lock = threading.RLock()
        self._forest = None
        self._config_dir = None
        self._overlay = {}
        self._stale = False
        self._roots = None

    def mark_stale(self, roots=None):
        """Пометить предсборку устаревшей; ``roots`` — точечный набор."""
        with self._lock:
            self._stale = True
            self._roots = (
                {Path(path) for path in roots}
                if roots is not None
                else None
            )

    def start(self, forest, config_dir, overlay=None, roots=None):
        """Поставить фоновую сборку LTX/XML в очередь."""
        with self._lock:
            self._forest = forest
            self._config_dir = config_dir
            self._overlay = overlay or {}
            self._stale = False
            self._roots = (
                {Path(path) for path in roots}
                if roots is not None
                else None
            )
            self.prebuild.start(
                forest,
                config_dir,
                overlay=overlay,
                roots=self._roots,
            )

    def ensure(self, forest=None, config_dir=None, overlay=None, roots=None):
        """Гарантировать актуальную предсборку перед чтением кеша."""
        with self._lock:
            forest = forest or self._forest
            config_dir = config_dir or self._config_dir
            if overlay is None:
                overlay = self._overlay
            if roots is None:
                roots = self._roots
            if forest is None or config_dir is None:
                return False

            status = self.prebuild.status()
            running = status.get("state") == "running"
            if self._stale or not running:
                if self._stale or status.get("state") != "done":
                    self.start(
                        forest,
                        config_dir,
                        overlay=overlay,
                        roots=roots,
                    )
                    return True
            return False

    def status(self):
        status = self.prebuild.status()
        status["cache"] = self.cache_stats()
        return status

    def cache_stats(self):
        if self.cache_service is None:
            return {}
        return self.cache_service.stats()

    def wait(self, log=None, timeout=None):
        self.prebuild.wait(log=log, timeout=timeout)

    def stop(self):
        self.prebuild.stop()


__all__ = ["PrebuildServices"]
