# -*- coding: utf-8 -*-
"""Инвалидация кэшей и связанных каталогов."""

from __future__ import annotations

from pathlib import Path


class CacheControl:
    """Явные операции инвалидации поверх узких сервисов ядра."""

    def __init__(
        self,
        cache_service,
        resolve,
        graph,
        data,
        prebuild,
    ):
        self.cache_service = cache_service
        self.resolve = resolve
        self.graph = graph
        self.data = data
        self.prebuild = prebuild

    def invalidate_cache(self):
        """Очистить общий кэш ядра и пометить предсборку устаревшей."""
        self.prebuild.mark_stale()
        if self.cache_service is not None:
            self.cache_service.clear()
        self.resolve.invalidate_cache()
        self.graph.clear_cache()
        self.data.clear_memory()

    def invalidate_resolver_cache(self):
        """Сбросить только резолвер/XML/граф, не трогая локализацию."""
        self.prebuild.mark_stale()
        self.resolve.invalidate_cache()
        self.graph.clear_cache()

    def invalidate_paths(self, roots):
        """Инвалидировать кэш только для заданных корней."""
        roots = {Path(path) for path in (roots or ())}
        self.prebuild.mark_stale(roots or None)
        if roots:
            self.resolve.invalidate_roots(roots)
        else:
            self.resolve.invalidate_cache()
        self.graph.clear_cache()

    def cache_stats(self):
        if self.cache_service is None:
            return {}
        return self.cache_service.stats()


__all__ = ["CacheControl"]
