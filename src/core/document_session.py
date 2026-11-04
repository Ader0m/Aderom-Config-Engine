# -*- coding: utf-8 -*-
"""Сессия документа: отслеживание правок и подготовка diff.

Сессия хранит исходный текст и текущий черновик. Она не пишет файлы и не
решает, куда сохранять. Её задача — честно ответить:

- изменён ли документ;
- что именно изменилось;
- можно ли построить план патча.

Отдельный :class:`FileDocumentTracker` следит за изменением файла на диске:
это нужно, чтобы заметить внешнюю правку и не строить патч по устаревшему
оригиналу.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .document_diff import DocumentDiff, diff_documents
from .document_model import LtxDocument, parse_ltx_document
from .fs import read_text_safe


class DocumentSession:
    """Исходный текст + текущий черновик одного LTX-документа.

    Сессия работает с текстом конкретного физического файла. Resolved-вид
    сюда передавать нельзя: это проекция нескольких файлов, и diff по ней
    не равен изменению одного исходника.
    """

    def __init__(self, source_path, baseline_text: str):
        self.source_path = Path(source_path)
        self._baseline_text = str(baseline_text or "")
        self._current_text = self._baseline_text

        self._baseline_document = parse_ltx_document(
            self._baseline_text,
            source_path=self.source_path,
        )
        self._current_document = self._baseline_document
        self.revision = 0

    @classmethod
    def from_file(cls, path):
        path = Path(path)
        return cls(path, read_text_safe(path))

    @property
    def baseline_text(self) -> str:
        return self._baseline_text

    @property
    def current_text(self) -> str:
        return self._current_text

    @property
    def baseline_document(self) -> LtxDocument:
        return self._baseline_document

    @property
    def current_document(self) -> LtxDocument:
        return self._current_document

    @property
    def is_dirty(self) -> bool:
        return self._current_text != self._baseline_text

    def set_text(self, text: str) -> bool:
        """Заменить черновик. Возвращает True, если состояние изменилось."""
        text = str(text or "")
        if text == self._current_text:
            return False

        self._current_text = text
        self._current_document = parse_ltx_document(
            text,
            source_path=self.source_path,
        )
        self.revision += 1
        return True

    def revert(self) -> bool:
        """Вернуть черновик к исходному тексту."""
        if not self.is_dirty:
            return False
        self.set_text(self._baseline_text)
        return True

    def mark_saved(self, text: Optional[str] = None) -> None:
        """Зафиксировать новый baseline.

        Для патч-конвейера это означает «черновик принят», а не «оригинал
        изменён». Оригинальный файл по-прежнему не трогается.
        """
        if text is not None:
            self._current_text = str(text or "")

        self._baseline_text = self._current_text
        self._baseline_document = parse_ltx_document(
            self._baseline_text,
            source_path=self.source_path,
        )
        self._current_document = self._baseline_document
        self.revision += 1

    def diff(self) -> DocumentDiff:
        """Семантическое сравнение baseline и черновика."""
        return diff_documents(
            self._baseline_document,
            self._current_document,
        )

    def plan(self, forest=None, options=None):
        """Построить план патча по текущему черновику."""
        from .patch_planner import PatchPlanner

        return PatchPlanner().plan_document_change(
            self.source_path,
            self._baseline_text,
            self._current_text,
            forest,
            options,
        )


class FileDocumentTracker:
    """Слежение за внешними изменениями файла.

    Хранит последний прочитанный текст и позволяет узнать, изменился ли
    файл на диске с момента загрузки.
    """

    def __init__(self, path):
        self.path = Path(path)
        self._text = ""
        self._loaded = False

    def load(self) -> str:
        self._text = read_text_safe(self.path)
        self._loaded = True
        return self._text

    @property
    def text(self) -> str:
        if not self._loaded:
            self.load()
        return self._text

    def refresh(self) -> bool:
        """Перечитать файл. True — содержимое изменилось."""
        if not self._loaded:
            self.load()
            return True

        current = read_text_safe(self.path)
        if current == self._text:
            return False

        self._text = current
        return True

    def session(self) -> DocumentSession:
        return DocumentSession(self.path, self.text)
