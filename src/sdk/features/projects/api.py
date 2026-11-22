# -*- coding: utf-8 -*-
"""Контракт проекта: настройки, asset-типы, читатели файлов."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Protocol  # noqa: F401

from sdk.features.documents.models import FileView
from sdk.features.projects.models import (
    AssetDraftInfo,
    ProjectAssetType,
    ProjectSetting,
)


class ProjectApi(Protocol):
    """Регистрация и чтение проектных расширений модуля."""

    def register_project_asset_type(self, asset_type: ProjectAssetType) -> bool:
        """Зарегистрировать тип двоичного ресурса проекта.

        Регистрация действует до выгрузки модуля. Конфликт ключа или пары
        ``(vfs_root, extension)`` не перезаписывает существующий тип.
        """

    def unregister_project_asset_type(self, key: str) -> bool:
        """Убрать тип ресурса, зарегистрированный модулем."""

    def project_asset_types(self) -> tuple[ProjectAssetType, ...]:
        """Все зарегистрированные типы двоичных ресурсов."""

    def register_project_setting(self, setting: ProjectSetting) -> bool:
        """Зарегистрировать настройку в окне настроек проекта.

        Регистрация действует до выгрузки модуля. Настройка показывается
        только у активного проекта; значение по умолчанию берётся из
        :attr:`ProjectSetting.default`.
        """

    def unregister_project_setting(self, key: str) -> bool:
        """Убрать настройку, зарегистрированную модулем."""

    def project_settings(self) -> tuple[ProjectSetting, ...]:
        """Все настройки проекта, зарегистрированные модулями."""

    def project_setting(self, key: str, default=None):
        """Текущее значение настройки активного проекта.

        Если проект не создан или значение ещё не сохранялось, возвращается
        ``default``, затем ``ProjectSetting.default``.
        """

    def set_project_setting(self, key: str, value) -> bool:
        """Сохранить значение настройки в активном проекте."""

    def register_file_reader(self, reader):
        """Зарегистрировать читатель файлов.

        Возвращает объект с методом ``unsubscribe()``. Читатель
        вызывается в фоновом потоке предпросмотра проекта, поэтому его
        ``read`` должен быть чистым и потокобезопасным.
        """

    def read_file_view(
        self,
        path: Path,
        data: Optional[bytes] = None,
    ) -> Optional[FileView]:
        """Получить представление файла от первого подходящего читателя."""

    def read_project_asset(self, path: Path) -> Optional[bytes]:
        """Текущие байты двоичного черновика или ``None``."""

    def update_project_asset(self, path: Path, data: bytes) -> bool:
        """Обновить двоичный черновик в памяти (без записи в проект)."""

    def save_project_asset(
        self,
        path: Path,
        data: Optional[bytes] = None,
    ) -> bool:
        """Записать двоичный черновик в каталог проекта сейчас."""

    def project_asset_drafts(self) -> tuple[AssetDraftInfo, ...]:
        """Двоичные черновики активного проекта."""
