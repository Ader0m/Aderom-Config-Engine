# -*- coding: utf-8 -*-
"""Предпочтения обработчиков открытия файлов.

Файл хранит только пользовательский выбор при неоднозначности. Формат
версионирован: файл несовместимой версии игнорируется целиком. Это не
кэш и не данные проекта: повреждённый файл игнорируется, отсутствие
означает «спросить или выбрать по приоритету».
"""

import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)

PREFERENCES_VERSION = 2


def preference_key(suffix, context):
    return "%s|%s" % (context, str(suffix or "").lower() or "*")


class FileOpenPreferences:
    """Предпочтения по расширению и контексту открытия."""

    def __init__(self, path=None):
        self._path = Path(path) if path is not None else None
        self._lock = threading.RLock()
        self._values = {}
        self._load()

    def _load(self):
        if self._path is None or not self._path.is_file():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning(
                "Не удалось прочитать предпочтения открытия %s: %s",
                self._path,
                exc,
            )
            return
        if not isinstance(data, dict):
            return
        try:
            version = int(data.get("version") or 0)
        except (TypeError, ValueError):
            version = 0
        if version != PREFERENCES_VERSION:
            log.warning(
                "Предпочтения открытия %s имеют несовместимую версию %s "
                "(ожидалось %s); файл игнорируется",
                self._path,
                version,
                PREFERENCES_VERSION,
            )
            return
        values = data.get("preferences")
        if not isinstance(values, dict):
            return
        with self._lock:
            self._values = {
                str(key): str(value)
                for key, value in values.items()
                if value
            }

    def _save(self):
        if self._path is None:
            return
        try:
            with self._lock:
                payload = json.dumps(
                    {
                        "version": PREFERENCES_VERSION,
                        "preferences": dict(self._values),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                self._path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self._path.with_suffix(
                    self._path.suffix + ".tmp"
                )
                temporary.write_text(payload, encoding="utf-8")
                os.replace(str(temporary), str(self._path))
        except Exception as exc:
            log.warning(
                "Не удалось сохранить предпочтения открытия %s: %s",
                self._path,
                exc,
            )

    def get(self, suffix, context):
        key = preference_key(suffix, context)
        with self._lock:
            return self._values.get(key, "")

    def set(self, suffix, context, handler_key):
        key = preference_key(suffix, context)
        with self._lock:
            self._values[key] = str(handler_key)
        self._save()

    def clear(self, suffix, context):
        key = preference_key(suffix, context)
        with self._lock:
            removed = self._values.pop(key, None)
        if removed is not None:
            self._save()
        return removed is not None
