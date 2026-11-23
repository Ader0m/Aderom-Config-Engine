# -*- coding: utf-8 -*-
"""Небольшие общие помощники для тестов семантической модели."""

from pathlib import Path

from core.cache.service import CacheService
from core.resolver.service import ResolverService
from core.tools.cache import FileCacheSystem, ResolvedCache
from core.tree.builder import TreeBuilder


def write_ltx(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def build_service(config_dir: Path):
    """Построить лес и сервис резолва для временного VFS.

    Возвращает ``(forest, resolver_service, cache_service)``. Кэш
    семантической модели живёт в ``CacheService`` и не пишется на диск.
    """
    cache = FileCacheSystem()
    forest = TreeBuilder(cache=cache).build(
        config_dir,
        config_dir / "system.ltx",
        use_cache=False,
    )
    cache_service = CacheService(resolved_cache=ResolvedCache())
    service = ResolverService(
        cache,
        ResolvedCache(),
        cache_service=cache_service,
    )
    return forest, service, cache_service
