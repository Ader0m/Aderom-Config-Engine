# -*- coding: utf-8 -*-
"""Реестр настроек проекта, которые добавляют модули.

Модуль описывает настройку через SDK (:class:`sdk.api.ProjectSetting`),
а платформа:

- показывает её в окне настроек активного проекта;
- хранит выбранное значение в ``project.json``;
- отдаёт значение обратно модулю через ``ctx.ui.project_setting(...)``.

Конкретные ключи и подписи не зашиты в UI: ogg-редактор добавляет свою
настройку так же, как это сможет сделать любой другой модуль.
"""

from __future__ import annotations

import threading
from typing import Dict, Iterable, Optional, Tuple

from sdk.api import ProjectSetting, ProjectSettingChoice


_SETTING_KINDS = ("choice", "bool", "text")


class ProjectSettingError(ValueError):
    """Настройку нельзя зарегистрировать или применить."""


def _normalize_choice(value) -> str:
    return str(value if value is not None else "")


class ProjectSettingsRegistry:
    """Набор описаний настроек, зарегистрированных модулями."""

    def __init__(self, settings: Iterable[ProjectSetting] = ()):
        self._settings: Dict[str, ProjectSetting] = {}
        self._owners: Dict[str, str] = {}
        self._lock = threading.RLock()
        for setting in settings:
            self.register(setting)

    # ------------------------------------------------------------ регистрация
    def register(
        self,
        setting: ProjectSetting,
        replace: bool = False,
        owner: Optional[str] = None,
    ) -> ProjectSetting:
        with self._lock:
            return self._register_locked(setting, replace, owner=owner)

    def _register_locked(
        self,
        setting: ProjectSetting,
        replace: bool = False,
        owner: Optional[str] = None,
    ) -> ProjectSetting:
        key = str(getattr(setting, "key", "") or "").strip()
        if not key:
            raise ProjectSettingError(
                "Ключ настройки проекта не может быть пустым"
            )

        owner_value = str(owner or "")
        existing = self._settings.get(key)
        if existing is not None and not replace:
            raise ProjectSettingError(
                "Настройка уже зарегистрирована: %s" % key
            )
        existing_owner = self._owners.get(key, "")
        if (
            existing is not None
            and replace
            and existing_owner
            and owner_value
            and existing_owner != owner_value
        ):
            raise ProjectSettingError(
                "Настройка %s принадлежит модулю %s"
                % (key, existing_owner)
            )

        kind = str(getattr(setting, "kind", "") or "choice").strip().lower()
        if kind not in _SETTING_KINDS:
            raise ProjectSettingError(
                "Неизвестный тип настройки %s: %s" % (key, kind)
            )

        choices = tuple(
            ProjectSettingChoice(
                value=_normalize_choice(
                    getattr(choice, "value", choice)
                ),
                label=str(
                    getattr(choice, "label", "")
                    or getattr(choice, "value", choice)
                    or ""
                ),
            )
            for choice in (getattr(setting, "choices", ()) or ())
        )

        if kind == "choice":
            if not choices:
                raise ProjectSettingError(
                    "Настройка-выбор %s не содержит вариантов" % key
                )
            values = {choice.value for choice in choices}
            default = _normalize_choice(
                getattr(setting, "default", None)
            )
            if not default:
                default = choices[0].value
            if default not in values:
                raise ProjectSettingError(
                    "Значение по умолчанию %r не входит в варианты %s"
                    % (default, key)
                )
        elif kind == "bool":
            default = bool(getattr(setting, "default", False))
            choices = ()
        else:
            default = str(getattr(setting, "default", "") or "")
            choices = ()

        normalized = ProjectSetting(
            key=key,
            label=str(getattr(setting, "label", "") or key),
            kind=kind,
            default=default,
            choices=choices,
            description=str(getattr(setting, "description", "") or ""),
        )
        self._settings[key] = normalized
        self._owners[key] = owner_value
        return normalized

    def unregister(self, key: str, owner: Optional[str] = None) -> bool:
        key = str(key or "").strip()
        with self._lock:
            if (
                owner is not None
                and self._owners.get(key, "") != str(owner)
            ):
                return False
            removed = self._settings.pop(key, None)
            self._owners.pop(key, None)
            return removed is not None

    # ------------------------------------------------------------ чтение
    def settings(self) -> Tuple[ProjectSetting, ...]:
        with self._lock:
            return tuple(
                sorted(
                    self._settings.values(),
                    key=lambda item: (item.label.lower(), item.key.lower()),
                )
            )

    def get(self, key: str) -> Optional[ProjectSetting]:
        with self._lock:
            return self._settings.get(str(key or "").strip())

    def default(self, key: str, fallback=None):
        setting = self.get(key)
        if setting is None:
            return fallback
        return setting.default

    # ------------------------------------------------------------ значения
    def normalize(self, key: str, value):
        setting = self.get(key)
        if setting is None:
            raise ProjectSettingError(
                "Настройка не зарегистрирована: %s" % key
            )

        if setting.kind == "bool":
            return bool(value)

        if setting.kind == "choice":
            normalized = _normalize_choice(value)
            values = {choice.value for choice in setting.choices}
            if normalized not in values:
                raise ProjectSettingError(
                    "Недопустимое значение %r для настройки %s"
                    % (value, key)
                )
            return normalized

        return str(value if value is not None else "")
