# -*- coding: utf-8 -*-
"""Хранилище проекта: ``project.json``, схема и one-time migration.

Проект — пользовательские данные, поэтому смена схемы выполняется только
вперёд: старый ``project.json`` читается, переводится в текущий формат и
перезаписывается один раз. Перед миграцией создаётся backup, запись
атомарная. Обратное чтение нового формата не поддерживается, постоянной
ветки совместимости нет.

Модуль не знает про Qt, UI и сценарии предпросмотра.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict

from .models import Project, ProjectError, normalize_save_mode

log = logging.getLogger(__name__)

# Раскладка каталога проекта. Единственный источник имён: сервис и
# миграция используют эти константы, а не собственные литералы.
META_NAME = "project.json"
DRAFTS_DIR = "drafts"
RESOLVED_DIR = "resolved"
ASSETS_DIR = "assets"
TABS_NAME = "tabs.json"

# v1 — плоский project.json без поля schema_version (до Этапа 3).
# v2 — schema_version и нормализованный save_mode.
# v3 — карта изменённых строк в черновиках; старые карты считаются лениво.
LEGACY_SCHEMA_VERSION = 1
PROJECT_SCHEMA_VERSION = 3

MIGRATION_BACKUP_PREFIX = ".migration_backup_"
_MIGRATION_BACKUP_ITEMS = (
    META_NAME,
    DRAFTS_DIR,
    RESOLVED_DIR,
    ASSETS_DIR,
    TABS_NAME,
)

# Поля, без которых проект нельзя построить. Проверяются до миграции:
# повреждённый проект не должен перезаписываться.
_META_REQUIRED_TEXT_FIELDS = ("id", "name", "save_mode", "xml_patch_mode")
_META_NON_EMPTY_FIELDS = ("id", "name")


def write_json_atomic(target, data) -> None:
    """Записать JSON через временный файл и ``os.replace``."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(
        target.name
        + ".tmp.%d.%d" % (os.getpid(), threading.get_ident())
    )
    try:
        tmp.write_text(
            json.dumps(data, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(str(tmp), str(target))
    except Exception:
        try:
            if tmp.is_file():
                tmp.unlink()
        except OSError:
            pass
        raise


class ProjectRepository:
    """Чтение/запись ``project.json`` и one-time migration схемы."""

    def __init__(self):
        # ``project.json`` пишет только репозиторий: черновики живут
        # отдельно и сериализуются своими замками.
        self._write_lock = threading.RLock()

    # -------------------------------------------------- чтение
    def read_meta(self, project_dir) -> Dict[str, Any]:
        """Прочитать ``project.json`` как есть, без миграции."""
        project_dir = Path(project_dir)
        path = project_dir / META_NAME
        if not path.is_file():
            raise ProjectError("Проект не найден: %s" % project_dir.name)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ProjectError("Не удалось прочитать проект: %s" % exc)
        if not isinstance(data, dict):
            raise ProjectError(
                "Не удалось прочитать проект: ожидался объект JSON"
            )
        return data

    @staticmethod
    def schema_version(meta) -> int:
        """Версия схемы проекта; отсутствие поля — legacy v1."""
        if not isinstance(meta, dict) or "schema_version" not in meta:
            return LEGACY_SCHEMA_VERSION
        try:
            return int(meta["schema_version"])
        except (TypeError, ValueError):
            return LEGACY_SCHEMA_VERSION

    @classmethod
    def validate_meta(cls, meta) -> None:
        """Проверить обязательные поля до миграции и сборки проекта.

        Раньше отсутствие поля давало ``KeyError``, который UI не ловил.
        Повреждённый проект должен давать понятный ``ProjectError`` и не
        перезаписываться.
        """
        if not isinstance(meta, dict):
            raise ProjectError("Проект повреждён: ожидался объект JSON")
        for field in _META_REQUIRED_TEXT_FIELDS:
            if field not in meta:
                raise ProjectError("Проект повреждён: нет поля %s" % field)
            value = meta[field]
            if not isinstance(value, str):
                raise ProjectError(
                    "Проект повреждён: поле %s должно быть строкой" % field
                )
            if field in _META_NON_EMPTY_FIELDS and not value.strip():
                raise ProjectError(
                    "Проект повреждён: поле %s не может быть пустым" % field
                )

    # -------------------------------------------------- запись
    def write_meta(self, project: Project) -> None:
        """Записать ``project.json`` текущей схемы."""
        data = {
            "schema_version": PROJECT_SCHEMA_VERSION,
            "id": project.id,
            "name": project.name,
            "created_at": project.created_at,
            "modules": list(project.modules),
            "save_mode": project.save_mode,
            "xml_patch_mode": project.xml_patch_mode,
            "module_settings": dict(project.module_settings),
        }
        project.root_dir.mkdir(parents=True, exist_ok=True)
        with self._write_lock:
            write_json_atomic(project.root_dir / META_NAME, data)

    # -------------------------------------------------- миграция
    def ensure_current(self, project_dir, meta) -> Dict[str, Any]:
        """Привести метаданные к текущей схеме.

        Возвращает актуальные метаданные. Новее текущей схемы — ошибка:
        старый код не имеет права «угадывать» чужой формат.
        """
        version = self.schema_version(meta)
        if version == PROJECT_SCHEMA_VERSION:
            return meta
        if version > PROJECT_SCHEMA_VERSION:
            raise ProjectError(
                "Проект создан более новой версией ACE (схема %s); "
                "обновите приложение" % version
            )
        return self._migrate(Path(project_dir), meta)

    def _migrate(self, project_dir: Path, meta) -> Dict[str, Any]:
        data = dict(meta)
        # v1 -> v2: устаревшее имя режима фиксируется один раз, чтобы
        # чтение больше не зависело от legacy-алиаса.
        if "save_mode" in data:
            data["save_mode"] = normalize_save_mode(data["save_mode"])
        data["schema_version"] = PROJECT_SCHEMA_VERSION

        backup_dir = self._backup(project_dir)
        try:
            with self._write_lock:
                write_json_atomic(project_dir / META_NAME, data)
        except OSError as exc:
            raise ProjectError(
                "Не удалось записать мигрированный проект: %s" % exc
            ) from exc
        log.info(
            "Проект %s: схема %s -> %s, backup %s",
            project_dir.name,
            self.schema_version(meta),
            PROJECT_SCHEMA_VERSION,
            backup_dir,
        )
        return data

    def _backup(self, project_dir: Path) -> Path:
        """Скопировать данные проекта перед миграцией."""
        stamp = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = project_dir / (MIGRATION_BACKUP_PREFIX + stamp)
        index = 2
        while backup_dir.exists():
            backup_dir = project_dir / (
                "%s%s_%d" % (MIGRATION_BACKUP_PREFIX, stamp, index)
            )
            index += 1

        try:
            backup_dir.mkdir(parents=True)
            for name in _MIGRATION_BACKUP_ITEMS:
                source = project_dir / name
                if source.is_file():
                    shutil.copy2(source, backup_dir / name)
                elif source.is_dir():
                    shutil.copytree(source, backup_dir / name)
        except OSError as exc:
            shutil.rmtree(backup_dir, ignore_errors=True)
            raise ProjectError(
                "Не удалось создать backup перед миграцией проекта: %s"
                % exc
            ) from exc
        return backup_dir


__all__ = [
    "ASSETS_DIR",
    "DRAFTS_DIR",
    "LEGACY_SCHEMA_VERSION",
    "META_NAME",
    "MIGRATION_BACKUP_PREFIX",
    "PROJECT_SCHEMA_VERSION",
    "ProjectRepository",
    "RESOLVED_DIR",
    "TABS_NAME",
    "write_json_atomic",
]
