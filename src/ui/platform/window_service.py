# -*- coding: utf-8 -*-
"""Сохранение раскладки окон.

Сервис хранит только геометрию и состояние платформенных окон. Он не
создаёт окна модулей и не восстанавливает их содержимое: если модуль
отсутствует, его запись в layout остаётся неиспользованной и не мешает
запуску.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from PyQt5.QtCore import QByteArray

log = logging.getLogger(__name__)

LAYOUT_VERSION = 1


def _encode_qba(value):
    if value is None:
        return ""
    try:
        return bytes(value).hex()
    except Exception:
        return ""


def _decode_qba(value):
    if not value:
        return None
    try:
        return QByteArray.fromHex(str(value).encode("ascii"))
    except Exception:
        return None


class WindowService:
    """Хранит раскладку главного окна и геометрию окон модулей."""

    def __init__(self, path=None):
        self._path = Path(path) if path is not None else None
        self._lock = threading.RLock()
        self._data = {
            "version": LAYOUT_VERSION,
            "main": {},
            "windows": {},
            "docks": {},
            "state": {},
        }
        self._load()

    # -------------------------------------------------- файл
    def _load(self):
        if self._path is None or not self._path.is_file():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("Не удалось прочитать раскладку %s: %s", self._path, exc)
            return
        if not isinstance(data, dict):
            return
        try:
            version = int(data.get("version") or 0)
        except (TypeError, ValueError):
            version = 0
        if version > LAYOUT_VERSION:
            log.warning(
                "Раскладка %s новее поддерживаемой версии (%s > %s)",
                self._path,
                version,
                LAYOUT_VERSION,
            )
            return
        with self._lock:
            main = data.get("main")
            windows = data.get("windows")
            docks = data.get("docks")
            state = data.get("state")
            if isinstance(main, dict):
                self._data["main"] = dict(main)
            if isinstance(windows, dict):
                self._data["windows"] = {
                    str(key): value
                    for key, value in windows.items()
                    if isinstance(value, dict)
                }
            if isinstance(docks, dict):
                self._data["docks"] = {
                    str(key): value
                    for key, value in docks.items()
                    if isinstance(value, dict)
                }
            if isinstance(state, dict):
                self._data["state"] = {
                    str(key): value
                    for key, value in state.items()
                    if isinstance(value, dict)
                }

    def _save(self):
        if self._path is None:
            return
        try:
            with self._lock:
                payload = json.dumps(
                    self._data,
                    ensure_ascii=False,
                    indent=2,
                )
                self._path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self._path.with_suffix(
                    self._path.suffix + ".tmp"
                )
                temporary.write_text(payload, encoding="utf-8")
                os.replace(str(temporary), str(self._path))
        except (OSError, TypeError, ValueError) as exc:
            log.warning("Не удалось сохранить раскладку %s: %s", self._path, exc)

    # -------------------------------------------------- главное окно
    def restore_main_window(self, window):
        with self._lock:
            main = dict(self._data.get("main") or {})
        restored = False
        geometry = _decode_qba(main.get("geometry"))
        if geometry is not None:
            try:
                restored = bool(window.restoreGeometry(geometry)) or restored
            except Exception:
                log.exception("Не удалось восстановить геометрию окна")
        state = _decode_qba(main.get("state"))
        if state is not None:
            try:
                restored = bool(window.restoreState(state)) or restored
            except Exception:
                log.exception("Не удалось восстановить состояние доков")
        splitter = getattr(window, "splitter", None)
        sizes = main.get("splitter")
        if splitter is not None and isinstance(sizes, list):
            try:
                values = [int(value) for value in sizes]
                if values and len(values) == splitter.count():
                    splitter.setSizes(values)
                    restored = True
            except (TypeError, ValueError):
                pass
        return restored

    def save_main_window(self, window):
        main = {}
        try:
            geometry = window.saveGeometry()
            main["geometry"] = _encode_qba(geometry)
        except Exception:
            log.exception("Не удалось сохранить геометрию окна")
        try:
            state = window.saveState()
            main["state"] = _encode_qba(state)
        except Exception:
            log.exception("Не удалось сохранить состояние доков")
        splitter = getattr(window, "splitter", None)
        if splitter is not None:
            try:
                main["splitter"] = [int(value) for value in splitter.sizes()]
            except Exception:
                pass
        with self._lock:
            self._data["main"] = main
        self._save()

    # -------------------------------------------------- доки
    def save_dock(
        self,
        key,
        owner,
        area,
        size,
        floating=False,
        closable=True,
        visible=True,
        state_key="",
    ):
        key = str(key or "")
        if not key:
            return False
        try:
            width, height = (int(size[0]), int(size[1]))
        except (TypeError, ValueError, IndexError):
            width, height = (0, 0)
        with self._lock:
            self._data.setdefault("docks", {})[key] = {
                "owner": str(owner or ""),
                "area": str(area or ""),
                "size": [width, height],
                "floating": bool(floating),
                "closable": bool(closable),
                "visible": bool(visible),
                "state_key": str(state_key or ""),
            }
        self._save()
        return True

    def load_dock(self, key):
        with self._lock:
            value = self._data.get("docks", {}).get(str(key or ""))
            return dict(value) if isinstance(value, dict) else {}

    def forget_dock(self, key):
        with self._lock:
            removed = self._data.get("docks", {}).pop(
                str(key or ""),
                None,
            )
        if removed is not None:
            self._save()
        return removed is not None

    # -------------------------------------------------- состояние содержимого
    def save_state(self, state_key, state):
        key = str(state_key or "")
        if not key or not isinstance(state, dict):
            return False
        try:
            json.dumps(state, ensure_ascii=False)
        except (TypeError, ValueError):
            return False
        with self._lock:
            self._data.setdefault("state", {})[key] = dict(state)
        self._save()
        return True

    def load_state(self, state_key):
        key = str(state_key or "")
        with self._lock:
            value = self._data.get("state", {}).get(key)
            return dict(value) if isinstance(value, dict) else {}

    # -------------------------------------------------- окна модулей
    def restore_module_window(self, key, window):
        key = str(key or "")
        if not key:
            return False
        with self._lock:
            entry = self._data.get("windows", {}).get(key)
            if not isinstance(entry, dict):
                return False
            geometry = _decode_qba(entry.get("geometry"))
        if geometry is None:
            return False
        try:
            window.restoreGeometry(geometry)
        except Exception:
            log.exception("Не удалось восстановить окно %s", key)
            return False
        return True

    def save_module_window(self, key, owner, window, state_key=""):
        key = str(key or "")
        if not key:
            return
        try:
            geometry = _encode_qba(window.saveGeometry())
        except Exception:
            geometry = ""
        with self._lock:
            self._data.setdefault("windows", {})[key] = {
                "owner": str(owner or ""),
                "geometry": geometry,
                "state_key": str(state_key or ""),
            }
        self._save()

    def forget_module_window(self, key):
        with self._lock:
            removed = self._data.setdefault("windows", {}).pop(
                str(key or ""),
                None,
            )
        if removed is not None:
            self._save()
        return removed is not None

    def module_window_keys(self):
        with self._lock:
            return tuple(self._data.get("windows", {}).keys())
