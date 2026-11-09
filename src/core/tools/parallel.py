# -*- coding: utf-8 -*-
"""Общие helpers для фоновых вычислений.

Резолв, предсборка и декодирование иконок — Python-код, поэтому потоки не
дают ускорения из-за GIL: используется пул процессов. Здесь собраны
правила выбора числа воркеров и проверка, можно ли безопасно поднять
``spawn``-пул.
"""

from __future__ import annotations

import os
import sys


def default_workers(env_var, max_workers=None) -> int:
    """Число процессов по умолчанию.

    Берётся ``env_var``, если он задан, иначе ~75% логических CPU, но
    минимум один остаётся системе/UI. ``max_workers`` ограничивает пул
    сверху (например, для мелких задач вроде иконок).
    """
    env = os.environ.get(env_var)
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass

    cpu = os.cpu_count() or 1
    if cpu <= 1:
        return 1

    workers = max(1, min(cpu - 1, int(cpu * 0.75)))
    if max_workers is not None:
        workers = min(max_workers, workers)
    return workers


def can_spawn_processes() -> bool:
    """Можно ли безопасно поднять ``spawn``-пул.

    Из ``python -c``/stdin и из замороженного приложения дочерний процесс
    не сможет импортировать ``__main__`` и зависнет навсегда. В этом
    случае лучше сразу работать последовательно.
    """
    if getattr(sys, "frozen", False):
        return False

    main = sys.modules.get("__main__")
    path = getattr(main, "__file__", None)
    if not path:
        return False
    if str(path).startswith("<"):
        return False
    return True
