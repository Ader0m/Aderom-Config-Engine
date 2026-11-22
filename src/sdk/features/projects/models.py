# -*- coding: utf-8 -*-
"""DTO проекта: настройки, asset-типы, сохранённые черновики."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional  # noqa: F401


@dataclass(frozen=True)
class AssetDraftInfo:
    """Двоичный черновик проекта (ресурс вне configs)."""

    path: Path
    is_new: bool = False
    changed: bool = False
    dirty: bool = False
    size: int = 0

@dataclass(frozen=True)
class ProjectAssetType:
    """Тип двоичного ресурса, который модуль добавляет в проект.

    Пример для звуков::

        ProjectAssetType(
            key="sound.ogg",
            label="Звук Ogg Vorbis",
            extensions=(".ogg",),
            vfs_root="sounds",
        )

    Реестр типов принадлежит платформе. Проект использует его для
    проверки пути, предпросмотра и разрешения записи в патч-мод, поэтому
    ``core`` не знает про конкретные расширения.
    """

    key: str
    label: str
    extensions: tuple[str, ...]
    vfs_root: str
    # Необязательная настройка проекта, выбирающая способ записи
    # ресурса в патч-мод. Пустая строка — тип не имеет режимов.
    save_mode_setting: str = ""
    save_modes: tuple[str, ...] = ("override",)

@dataclass(frozen=True)
class ProjectSettingChoice:
    """Один вариант настройки проекта типа ``choice``."""

    value: str
    label: str

@dataclass(frozen=True)
class ProjectSetting:
    """Настройка, которую модуль добавляет в окно настроек проекта.

    Ключ, подпись, варианты и значение по умолчанию задаёт сам модуль;
    SDK знает только форму описания. Пример::

        ProjectSetting(
            key="example_module.mode",
            label="Режим модуля",
            kind="choice",
            default="default",
            choices=(
                ProjectSettingChoice(
                    value="default",
                    label="Обычный режим",
                ),
            ),
        )

    Платформа хранит выбранные значения в ``project.json`` и отдаёт их
    модулю через ``ctx.ui.project_setting(key)``.
    """

    key: str
    label: str
    kind: str = "choice"          # choice | bool | text
    default: object = None
    choices: tuple[ProjectSettingChoice, ...] = ()
    description: str = ""

@dataclass(frozen=True)
class ProjectChanged:
    """Лёгкий payload события ``ui.project_changed``."""

    project_id: str = ""
    name: str = ""
    active: bool = False

@dataclass(frozen=True)
class SavedDraft:
    """Сохранённый черновик проекта (payload ``PROJECT_SAVED``).

    ``kind``: ``file`` | ``resolved`` | ``asset``. Содержимое в событии
    не передаётся; ``revision``/``size`` позволяют отличить снимок.
    """

    kind: str
    key: str
    path: Path
    revision: int = 0
    size: int = 0
