# -*- coding: utf-8 -*-
"""Пользовательское включение/отключение модулей (ADR-31, ADR-34).

``config/modules.json`` хранит только намерение ``enabled`` и версию
схемы. Runtime-состояния загрузчика здесь не сохраняются. Файл является
локальным runtime-конфигом приложения, а не данными проекта.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

CONFIG_SCHEMA_VERSION = 1


class ModuleConfigStore:
    """Чтение и атомарная запись ``config/modules.json``."""

    def __init__(self, path, log=None):
        self._path = Path(path) if path is not None else None
        self._log = log or (lambda message: None)
        self._lock = threading.RLock()
        self._modules = {}
        self.schema_version = CONFIG_SCHEMA_VERSION
        self.read_only = False
        self.broken = False
        self._load()

    # -------------------------------------------------- чтение
    def _load(self):
        if self._path is None or not self._path.is_file():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self._mark_broken(
                "не удалось прочитать %s: %s" % (self._path, exc)
            )
            return

        if not isinstance(data, dict):
            self._mark_broken("%s должен содержать JSON-объект" % self._path)
            return

        schema_version = data.get("schema_version")
        if isinstance(schema_version, bool) or not isinstance(
            schema_version,
            int,
        ):
            self._mark_broken("schema_version отсутствует или не целое")
            return
        self.schema_version = schema_version

        if schema_version > CONFIG_SCHEMA_VERSION:
            self.read_only = True
            self._log(
                "config/modules.json создан более новой версией (%s); "
                "применяются defaults, запись заблокирована"
                % schema_version
            )
            return
        if schema_version != CONFIG_SCHEMA_VERSION:
            self._mark_broken(
                "неподдерживаемая schema_version=%s" % schema_version
            )
            return

        modules = data.get("modules", {})
        if not isinstance(modules, dict):
            self._mark_broken("поле modules должно быть объектом")
            return

        parsed = {}
        for module_id, value in modules.items():
            if not isinstance(module_id, str) or not module_id:
                self._mark_broken("id модуля должен быть непустой строкой")
                return
            if not isinstance(value, dict):
                self._mark_broken(
                    "запись модуля %s должна быть объектом" % module_id
                )
                return
            enabled = value.get("enabled")
            if not isinstance(enabled, bool):
                self._mark_broken(
                    "modules.%s.enabled должен быть boolean" % module_id
                )
                return
            parsed[module_id] = {"enabled": enabled}
        self._modules = parsed

    def _mark_broken(self, detail):
        self.broken = True
        self._log(
            "config/modules.json повреждён: %s; применены defaults"
            % detail
        )
        if self._path is None or not self._path.is_file():
            return
        backup = self._broken_backup_path()
        try:
            os.replace(str(self._path), str(backup))
        except OSError as exc:
            self._log(
                "config/modules.json: не удалось сохранить копию %s: %s"
                % (backup.name, exc)
            )
            return
        self._log(
            "config/modules.json: повреждённый файл сохранён как %s"
            % backup.name
        )

    def _broken_backup_path(self):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        candidate = self._path.with_name(
            self._path.name + ".broken-" + stamp
        )
        counter = 1
        while candidate.exists():
            candidate = self._path.with_name(
                self._path.name + ".broken-%s-%d" % (stamp, counter)
            )
            counter += 1
        return candidate

    # -------------------------------------------------- доступ
    def enabled_for(self, module_id, default):
        """Намерение пользователя или ``enabled_by_default``."""
        with self._lock:
            entry = self._modules.get(str(module_id))
        if entry is None:
            return bool(default)
        return bool(entry["enabled"])

    def modules(self):
        with self._lock:
            return {
                key: dict(value)
                for key, value in self._modules.items()
            }

    # -------------------------------------------------- запись
    def set_enabled(self, module_id, enabled):
        """Сохранить явное намерение; false при read-only/future-схеме."""
        module_id = str(module_id or "")
        if not module_id:
            return False
        with self._lock:
            if self.read_only:
                self._log(
                    "config/modules.json read-only: включение/отключение "
                    "модуля %s заблокировано" % module_id
                )
                return False
            previous = dict(self._modules)
            self._modules[module_id] = {"enabled": bool(enabled)}
            try:
                self._write()
            except OSError as exc:
                self._modules = previous
                self._log(
                    "config/modules.json: ошибка записи: %s" % exc
                )
                return False
            return True

    def _write(self):
        if self._path is None:
            raise OSError("путь config/modules.json не задан")
        payload = {
            "schema_version": CONFIG_SCHEMA_VERSION,
            "modules": {
                key: {"enabled": bool(value["enabled"])}
                for key, value in sorted(self._modules.items())
            },
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(
            self._path.name
            + ".tmp.%d.%d" % (os.getpid(), threading.get_ident())
        )
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(str(temporary), str(self._path))
        except Exception:
            try:
                if temporary.is_file():
                    temporary.unlink()
            except OSError:
                pass
            raise
