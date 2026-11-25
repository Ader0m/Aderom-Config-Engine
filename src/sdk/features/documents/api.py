# -*- coding: utf-8 -*-
"""Контракт документов: текст проекта, resolved-документы, подписки."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Protocol, Sequence

from sdk.features.documents.models import (
    LineSource,
    ResolvedDocumentChange,
    ResolvedDraftInfo,
    ResolvedLineEdit,
)


class DocumentViewerApi(Protocol):
    """Capability просмотра документов конкретного формата."""

    def open_document(self, path: Path, line_number=None) -> bool:
        """Открыть документ в режиме просмотра/резолва."""

    def open_raw_document(self, path: Path, line_number=None) -> bool:
        """Открыть исходный текст документа."""

    def open_resolved_document(self, path: Path, line_number=None) -> bool:
        """Открыть resolved-представление документа."""


class DocumentApi(Protocol):
    """Документы проекта и живые resolved-документы."""

    def read_project_text(self, path: Path) -> str:
        """Текст файла из черновика проекта (или с диска, если черновика нет)."""

    def write_project_text(self, path: Path, text: str) -> bool:
        """Записать текст в черновик проекта, не меняя оригинальный файл."""

    def write_project_text_async(
        self,
        path: Path,
        text: str,
        on_done=None,
        original_text: Optional[str] = None,
    ) -> bool:
        """Записать текстовый черновик в фоновом потоке.

        На один файл одновременно выполняется только одна запись;
        повторные вызовы для того же пути ставятся в очередь, и
        сохраняется последний текст. ``on_done(ok, error)`` вызывается в
        UI-потоке после завершения записи. ``original_text`` позволяет
        не перечитывать исходный файл при создании первого черновика.
        """

    def write_project_texts(self, files) -> bool:
        """Атомарно записать несколько текстовых черновиков проекта."""

    def resolved_draft(self, tab_id: str) -> Optional[ResolvedDraftInfo]:
        """Текущий resolved-черновик проекта по id вкладки."""

    def has_resolved_document(self, tab_id: str) -> bool:
        """Есть ли живой resolved-документ по id вкладки."""

    def resolved_document_title(self, tab_id: str) -> str:
        """Заголовок resolved-документа (для карточки и тултипов)."""

    def apply_resolved_line_edit(
        self,
        tab_id: str,
        action: str,
        line_number: int,
        text: str = "",
    ) -> bool:
        """Точечно изменить живой resolved-документ.

        Возвращает ``False``, если строка/документ не найдены или
        изменение не удалось зафиксировать.
        """

    def apply_resolved_field_edit(
        self,
        tab_id: str,
        section: str,
        key: str,
        value: str,
    ) -> bool:
        """Заменить или добавить поле в секции resolved-документа."""

    def rename_resolved_field(
        self,
        tab_id: str,
        section: str,
        old_key: str,
        new_key: str,
        value: str,
    ) -> bool:
        """Переименовать поле, найдя его по секции и старому ключу."""

    def apply_resolved_field_edit_async(
        self,
        tab_id: str,
        section: str,
        key: str,
        value: str,
        on_done=None,
    ) -> bool:
        """Поставить правку поля в фоновую очередь.

        ``on_done(ok, error)`` вызывается в UI-потоке после фиксации
        правки в документе. Возвращает ``False``, если очередь
        недоступна и нужен синхронный путь.
        """

    def rename_resolved_field_async(
        self,
        tab_id: str,
        section: str,
        old_key: str,
        new_key: str,
        value: str,
        on_done=None,
    ) -> bool:
        """Фоновая версия :meth:`rename_resolved_field`."""

    def undo_resolved_document(self, tab_id: str) -> bool:
        """Отменить последнюю правку resolved-документа."""

    def redo_resolved_document(self, tab_id: str) -> bool:
        """Повторить последнюю отменённую правку документа."""

    def resolved_section_at_line(self, tab_id: str, line_number: int):
        """Имя секции по номеру строки resolved-документа."""

    def retain_resolved_document(self, tab_id: str) -> bool:
        """Удержать документ, пока карточка открыта."""

    def release_resolved_document(self, tab_id: str) -> bool:
        """Освободить документ после закрытия карточки."""

    def update_resolved_draft(
        self,
        tab_id: str,
        root_path: Path,
        title: str,
        original_text: str,
        current_text: str,
        line_sources: Sequence[Optional[LineSource]] = (),
        line_edit: Optional[ResolvedLineEdit] = None,
    ) -> bool:
        """Обновить resolved-черновик проекта (без записи в оригинал).

        ``line_edit`` позволяет обновить уже открытую resolved-вкладку
        точечно, не заменяя весь текст (он может быть в десятки мегабайт).
        """

    def sync_resolved_tab(
        self,
        tab_id: str,
        text: str,
        line_sources: Sequence[Optional[LineSource]],
        root_path: Path,
    ) -> None:
        """Обновить уже открытую resolved-вкладку, не переключая фокус."""

    def open_resolved_tab(
        self,
        tab_id: str,
        title: str,
        text: str,
        line_sources: Sequence[Optional[LineSource]],
        root_path: Path,
        line_number: Optional[int] = None,
        request_id: Optional[int] = None,
    ) -> None:
        """Открыть вкладку резолв-вида с панелью источников.

        ``request_id`` связывает событие ``FILE_OPENED`` с запросом
        открытия. Асинхронный обработчик должен передать id, полученный
        из ``ctx.ui.current_file_request_id()``.
        """

    def ensure_resolved_document(
        self,
        tab_id: str,
        root_path: Path,
        title: str = "",
        text: str = "",
        line_sources: Sequence[Optional[LineSource]] = (),
    ) -> None:
        """Создать/получить живой resolved-документ по id вкладки."""

    def open_resolved_document(
        self,
        tab_id: str,
        line_number: Optional[int] = None,
    ) -> bool:
        """Открыть или сфокусировать resolved-документ по id вкладки."""

    def open_xml_resolved_tab(
        self,
        tab_id: str,
        title: str,
        text: str,
        root_path: Path,
        line_sources: Sequence[Optional[LineSource]] = (),
        line_number: Optional[int] = None,
        request_id: Optional[int] = None,
    ) -> None:
        """Открыть вкладку с результатом XML-резолва.

        ``line_sources`` — владельцы строк в том же формате, что у LTX.
        ``request_id`` связывает событие ``FILE_OPENED`` с запросом.
        """

    def open(self, path: Path, line_number: Optional[int] = None) -> None:
        """Открыть документ в рабочей среде."""

    def read(self, path: Path) -> str:
        """Прочитать текст документа проекта."""

    def apply(self, tab_id: str, edit: ResolvedLineEdit) -> bool:
        """Применить точечную правку к resolved-документу."""

    def subscribe(self, handler: Callable) -> object:
        """Подписаться на изменения resolved-документов."""
