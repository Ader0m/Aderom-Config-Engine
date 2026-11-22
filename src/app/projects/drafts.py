# -*- coding: utf-8 -*-
"""Хранилище черновиков проекта: текстовые, resolved и бинарные.

Модуль отвечает за формат файлов черновиков, их имена, сериализацию и
атомарную запись. Он не знает про активный проект, overlay, предпросмотр
и Qt: этим владеет ``ProjectService``.

Формат не меняется. Версии формата поднимаются только вместе с
one-time migration проекта.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import sys
import threading
import zlib
from array import array
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.api.documents import LineSource
from core.api.paths import path_key

from app.documents import collapse_duplicated_text

from .models import (
    ProjectAssetDraft,
    ProjectDraft,
    ProjectResolvedDraft,
    resolved_key,
)
from .repository import (
    ASSETS_DIR,
    DRAFTS_DIR,
    RESOLVED_DIR,
    write_json_atomic,
)

log = logging.getLogger(__name__)

# Крупные тексты храним в JSON сжатыми: system.ltx — это десятки
# мегабайт, а plain JSON раздувает его и диск, и время автосохранения.
_TEXT_COMPRESSION_THRESHOLD = 16 * 1024

# resolved-черновик: уникальные LineSource + сжатый массив индексов на
# строку и zlib-сжатие крупных текстовых полей.
RESOLVED_DRAFT_VERSION = 3
LINE_SOURCE_IDS_VERSION = 2

_ZLIB_BASE64 = "zlib+base64"


def _pack_text(value) -> object:
    """Упаковать текст для JSON: маленькие строки остаются как есть."""
    text = str(value or "")
    raw = text.encode("utf-8", "replace")
    if len(raw) < _TEXT_COMPRESSION_THRESHOLD:
        return text
    return {
        "encoding": _ZLIB_BASE64,
        "data": base64.b64encode(zlib.compress(raw, 1)).decode("ascii"),
    }


def _unpack_text(value, default="") -> str:
    """Прочитать текстовое поле нового или старого формата."""
    if isinstance(value, dict):
        if value.get("encoding") != _ZLIB_BASE64:
            return str(default or "")
        try:
            raw = zlib.decompress(
                base64.b64decode(value.get("data") or "")
            )
            return raw.decode("utf-8", "replace")
        except Exception:
            log.warning("Проект: повреждено сжатое текстовое поле")
            return str(default or "")
    if value is None:
        return str(default or "")
    return str(value)


def _read_text_field(data, key, default=""):
    """Прочитать текстовое поле старого (plain) или нового формата."""
    if key not in data:
        return default
    return _unpack_text(data.get(key), default=default)


def _line_source_record(source) -> dict:
    path = getattr(source, "path", None)
    return {
        "kind": str(getattr(source, "kind", "") or ""),
        "display": str(getattr(source, "display", "") or ""),
        "path": str(path) if path is not None else "",
        "line": int(getattr(source, "line", 0) or 0),
        "open_raw": bool(getattr(source, "open_raw", False)),
    }


def _line_source_key(record):
    if record is None:
        return None
    return (
        str(record.get("kind") or ""),
        str(record.get("display") or ""),
        str(record.get("path") or ""),
        int(record.get("line") or 0),
        bool(record.get("open_raw")),
    )


def _line_source_from_record(record):
    if record is None:
        return None
    path = str(record.get("path") or "")
    return LineSource(
        kind=str(record.get("kind") or "none"),
        display=str(record.get("display") or ""),
        path=Path(path) if path else None,
        line=int(record.get("line") or 0),
        open_raw=bool(record.get("open_raw")),
    )


def _serialize_line_sources(line_sources) -> dict:
    """Уникальные источники + сжатый массив индексов на каждую строку.

    Старый формат писал по объекту на строку. Для system.ltx это
    миллион объектов и сотни мегабайт, хотя уникальных источников —
    порядка тысячи. Индекс 0 означает ``None``; источник с индексом
    ``n`` лежит в ``sources[n - 1]``.
    """
    records = []
    record_index = {}
    ids = array("I")

    for source in line_sources or ():
        if source is None:
            ids.append(0)
            continue

        record = _line_source_record(source)
        key = _line_source_key(record)
        index = record_index.get(key)
        if index is None:
            records.append(record)
            index = len(records)
            record_index[key] = index
        ids.append(index)

    if sys.byteorder != "little":
        ids.byteswap()

    packed = zlib.compress(ids.tobytes(), 6)
    return {
        "version": LINE_SOURCE_IDS_VERSION,
        "sources": records,
        "ids": base64.b64encode(packed).decode("ascii"),
    }


def _deserialize_line_sources(values) -> tuple:
    """Прочитать новый компактный формат или старый список объектов."""
    if isinstance(values, dict):
        return _deserialize_line_sources_v2(values)

    cache = {}
    result = []
    for data in values or ():
        if not isinstance(data, dict):
            # None — валидная запись «источник неизвестен». Нельзя
            # выбрасывать её: иначе индексы строк сдвинутся.
            result.append(None)
            continue
        key = _line_source_key(data)
        source = cache.get(key)
        if source is None:
            source = _line_source_from_record(data)
            if key is not None:
                cache[key] = source
        result.append(source)
    return tuple(result)


def _deserialize_line_sources_v2(values) -> tuple:
    records = values.get("sources") or ()
    parsed = tuple(
        _line_source_from_record(record)
        for record in records
        if isinstance(record, dict)
    )

    try:
        packed = base64.b64decode(values.get("ids") or "")
        ids = array("I")
        ids.frombytes(zlib.decompress(packed))
        if sys.byteorder != "little":
            ids.byteswap()
    except Exception:
        return ()

    return tuple(
        0 < index <= len(parsed) and parsed[index - 1] or None
        for index in ids
    )


def _unlink(path) -> None:
    """Удалить файл черновика, если он есть; ошибки не пробрасывать."""
    try:
        path = Path(path)
        if path.is_file():
            path.unlink()
    except OSError:
        pass


class DraftStore:
    """Файловое хранилище черновиков проекта."""

    def __init__(self, write_lock=None):
        # Один замок на все файлы проекта: его передаёт ProjectService,
        # чтобы автосохранение и фоновая запись не писали одновременно.
        self._write_lock = (
            write_lock if write_lock is not None else threading.RLock()
        )

    # -------------------------------------------------- текстовые
    @staticmethod
    def text_filename(path) -> str:
        digest = hashlib.sha1(
            path_key(path).encode("utf-8", "replace")
        ).hexdigest()
        return digest + ".json"

    def text_target(self, project_dir, path) -> Path:
        return Path(project_dir) / DRAFTS_DIR / self.text_filename(path)

    @staticmethod
    def build_text_payload(
        draft: ProjectDraft,
        saved_text=None,
        saved_at=None,
    ) -> Dict[str, object]:
        current = str(draft.current_text or "")
        original = str(draft.original_text or "")
        saved = current if saved_text is None else str(saved_text)
        payload = {
            "path": str(draft.path),
            "current_text": _pack_text(current),
            "is_new": bool(draft.is_new),
            "revision": int(draft.revision),
            "updated_at": draft.updated_at,
            "saved_at": (
                draft.saved_at if saved_at is None else float(saved_at)
            ),
        }
        if original != current:
            payload["original_text"] = _pack_text(original)
        if saved != current:
            payload["saved_text"] = _pack_text(saved)
        return payload

    def read_text_drafts(self, project_dir) -> List[ProjectDraft]:
        folder = Path(project_dir) / DRAFTS_DIR
        if not folder.is_dir():
            return []
        result = []
        for draft_path in sorted(folder.glob("*.json")):
            draft = self._read_text_draft(draft_path)
            if draft is not None:
                result.append(draft)
        return result

    def delete_text(self, project_dir, path) -> None:
        _unlink(self.text_target(project_dir, path))

    @staticmethod
    def _read_text_draft(draft_path: Path) -> Optional[ProjectDraft]:
        try:
            data = json.loads(draft_path.read_text(encoding="utf-8"))
            path = Path(data["path"])
            current = _read_text_field(data, "current_text")
            original = _read_text_field(
                data,
                "original_text",
                default=current,
            )
            saved = _read_text_field(
                data,
                "saved_text",
                default=current,
            )
            return ProjectDraft(
                path=path,
                original_text=original,
                current_text=current,
                saved_text=saved,
                is_new=bool(data["is_new"]),
                revision=int(data.get("revision") or 0),
                updated_at=float(data.get("updated_at") or 0.0),
                saved_at=float(data.get("saved_at") or 0.0),
            )
        except Exception as exc:
            log.warning(
                "Проект: не удалось прочитать черновик %s: %s",
                draft_path,
                exc,
            )
            return None

    # -------------------------------------------------- бинарные
    @staticmethod
    def asset_filename(path) -> str:
        digest = hashlib.sha1(
            path_key(path).encode("utf-8", "replace")
        ).hexdigest()
        return digest + ".json"

    def asset_target(self, project_dir, path) -> Path:
        return Path(project_dir) / ASSETS_DIR / self.asset_filename(path)

    @staticmethod
    def build_asset_payload(
        draft: ProjectAssetDraft,
        saved_bytes=None,
        saved_at=None,
    ) -> Dict[str, object]:
        saved = (
            draft.saved_bytes if saved_bytes is None else bytes(saved_bytes)
        )
        data = {
            "path": str(draft.path),
            "original_sha1": draft.original_sha1,
            "current_b64": base64.b64encode(draft.current_bytes).decode(
                "ascii"
            ),
            "is_new": bool(draft.is_new),
            "revision": int(draft.revision),
            "updated_at": draft.updated_at,
            "saved_at": (
                draft.saved_at if saved_at is None else float(saved_at)
            ),
        }
        # saved == current после обычного сохранения: не пишем вторую
        # копию бинарника.
        if saved != draft.current_bytes:
            data["saved_b64"] = base64.b64encode(saved).decode("ascii")
        return data

    def read_asset_drafts(self, project_dir) -> List[ProjectAssetDraft]:
        folder = Path(project_dir) / ASSETS_DIR
        if not folder.is_dir():
            return []
        result = []
        for draft_path in sorted(folder.glob("*.json")):
            draft = self._read_asset_draft(draft_path)
            if draft is not None:
                result.append(draft)
        return result

    def delete_asset(self, project_dir, path) -> None:
        _unlink(self.asset_target(project_dir, path))

    @staticmethod
    def _read_asset_draft(
        draft_path: Path,
    ) -> Optional[ProjectAssetDraft]:
        try:
            data = json.loads(draft_path.read_text(encoding="utf-8"))
            current = base64.b64decode(data.get("current_b64") or "")
            saved = (
                base64.b64decode(data["saved_b64"])
                if "saved_b64" in data
                else current
            )
            return ProjectAssetDraft(
                path=Path(data["path"]),
                original_sha1=str(data.get("original_sha1") or ""),
                current_bytes=current,
                saved_bytes=saved,
                is_new=bool(data.get("is_new")),
                revision=int(data.get("revision") or 0),
                updated_at=float(data.get("updated_at") or 0.0),
                saved_at=float(data.get("saved_at") or 0.0),
            )
        except Exception as exc:
            log.warning(
                "Проект: не удалось прочитать двоичный черновик %s: %s",
                draft_path,
                exc,
            )
            return None

    # -------------------------------------------------- resolved
    @staticmethod
    def resolved_filename(draft: ProjectResolvedDraft) -> str:
        digest = hashlib.sha1(
            resolved_key(draft.tab_id, draft.root_path).encode(
                "utf-8",
                "replace",
            )
        ).hexdigest()
        return digest + ".json"

    def resolved_target(
        self,
        project_dir,
        draft: ProjectResolvedDraft,
        existing=None,
    ) -> Path:
        if existing is not None:
            return Path(existing)
        return (
            Path(project_dir)
            / RESOLVED_DIR
            / self.resolved_filename(draft)
        )

    @staticmethod
    def build_resolved_payload(
        draft: ProjectResolvedDraft,
        saved_text=None,
        saved_at=None,
    ) -> Dict[str, object]:
        current = str(draft.current_text or "")
        original = str(draft.original_text or "")
        saved = current if saved_text is None else str(saved_text)
        if saved == current + current:
            saved = current

        data = {
            "version": RESOLVED_DRAFT_VERSION,
            "tab_id": draft.tab_id,
            "root_path": str(draft.root_path),
            "title": draft.title,
            "current_text": _pack_text(current),
            "line_sources": _serialize_line_sources(draft.line_sources),
            "revision": int(draft.revision),
            "updated_at": draft.updated_at,
            "saved_at": (
                draft.saved_at if saved_at is None else float(saved_at)
            ),
        }
        # Обычный случай после автосохранения: original == current.
        # Не храним вторую копию; при чтении восстановим её.
        if original != current:
            data["original_text"] = _pack_text(original)
        if saved != current:
            data["saved_text"] = _pack_text(saved)
        return data

    def read_resolved_drafts(
        self,
        project_dir,
    ) -> List[Tuple[Path, ProjectResolvedDraft]]:
        folder = Path(project_dir) / RESOLVED_DIR
        if not folder.is_dir():
            return []
        result = []
        for draft_path in sorted(folder.glob("*.json")):
            draft = self._read_resolved_draft(draft_path)
            if draft is not None:
                result.append((draft_path, draft))
        return result

    def delete_resolved(self, target) -> None:
        _unlink(target)

    @staticmethod
    def _read_resolved_draft(
        draft_path: Path,
    ) -> Optional[ProjectResolvedDraft]:
        try:
            data = json.loads(draft_path.read_text(encoding="utf-8"))
            raw_current = _read_text_field(data, "current_text")
            original_text = _read_text_field(
                data,
                "original_text",
                default=raw_current,
            )
            current_text = collapse_duplicated_text(
                raw_current,
                original_text,
            )
            saved_text = _read_text_field(
                data,
                "saved_text",
                default=raw_current,
            )
            if saved_text == raw_current:
                saved_text = current_text
            else:
                saved_text = collapse_duplicated_text(
                    saved_text,
                    original_text,
                )
            return ProjectResolvedDraft(
                root_path=Path(data["root_path"]),
                title=str(data.get("title") or ""),
                original_text=original_text,
                current_text=current_text,
                saved_text=saved_text,
                tab_id=str(data.get("tab_id") or ""),
                line_sources=_deserialize_line_sources(
                    data.get("line_sources")
                ),
                revision=int(data.get("revision") or 0),
                updated_at=float(data.get("updated_at") or 0.0),
                saved_at=float(data.get("saved_at") or 0.0),
            )
        except Exception as exc:
            log.warning(
                "Проект: не удалось прочитать resolved-черновик %s: %s",
                draft_path,
                exc,
            )
            return None

    # -------------------------------------------------- запись
    def write_payload(self, target, payload) -> None:
        """Атомарно записать черновик под общим замком проекта."""
        with self._write_lock:
            write_json_atomic(target, payload)


__all__ = [
    "DraftStore",
    "LINE_SOURCE_IDS_VERSION",
    "RESOLVED_DRAFT_VERSION",
]
