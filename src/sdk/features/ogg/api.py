# -*- coding: utf-8 -*-
"""Capability ``audio.ogg.editor``: редактор звуков .ogg."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class OggEditorApi(Protocol):
    """Контракт capability ``audio.ogg.editor``.

    Провайдер — модуль ``ogg_editor``.
    """

    def open_file(self, path: Path) -> None:
        """Открыть .ogg в редакторе."""

    def add_file(self) -> None:
        """Добавить .ogg в проект через диалог редактора."""
