# -*- coding: utf-8 -*-
"""Общие текстовые helpers ядра."""

from __future__ import annotations


def safe_filename_component(value) -> str:
    """Безопасный фрагмент имени файла для generated-каталогов.

    Оставляет буквы/цифры/``_``/``-``, остальное заменяет на ``_``.
    Регистр приводится к нижнему.
    """
    return "".join(
        char if char.isalnum() or char in "_-" else "_"
        for char in str(value)
    ).lower()
