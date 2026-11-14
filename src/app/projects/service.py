# -*- coding: utf-8 -*-
"""Проект пользователя: черновики файлов и подготовка патча.

Проект — это сессия редактирования. Он хранит новые версии файлов, но не
меняет оригиналы. Пока проект активен, файлы можно редактировать; без
проекта редактор работает только для чтения.

Проект не требует ручного «Сохранить проект»: изменения автоматически
пишутся в каталог проекта при Ctrl+S, раз в минуту и при закрытии
приложения. Это защищает черновики от потери, но не создаёт патч-мод.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import threading
import time
import traceback
import zlib
from array import array
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from core import (
    GeneratedPatch,
    LineSource,
    PatchFile,
    PatchIssue,
    PatchOptions,
    PatchPlan,
    PatchTarget,
    VerificationCheck,
    VerificationResult,
    analyze_multiline_edit,
    merge_patch_plans,
    read_text_safe,
    read_xml_text,
)

from sdk.api import SavedDraft

from app.documents import collapse_duplicated_text

from .assets import (
    ProjectAssetRegistry,
    ProjectAssetTypeError,
)
from .readers import FileReaderError, ProjectFileReaderRegistry


from .patch_builder import ProjectPreviewMixin
from .models import (
    AssetChange,
    PreviewItem,
    Project,
    ProjectAssetDraft,
    ProjectDraft,
    ProjectError,
    ProjectInfo,
    ProjectPatchPreview,
    ProjectResolvedDraft,
    SAVE_MODES,
    _LEGACY_SAVE_MODES,
    normalize_save_mode,
    resolved_key,
)

class ProjectService(ProjectPreviewMixin):
    """Хранилище проектов и черновиков."""

    META_NAME = "project.json"
    DRAFTS_DIR = "drafts"
    RESOLVED_DIR = "resolved"
    ASSETS_DIR = "assets"
    ROOTS_NAME = "project_roots.json"

    PREVIEW_CACHE_SIZE = 128

    # Версия формата resolved-черновика. Старые файлы читаются как есть,
    # но при следующем сохранении переписываются в компактный формат:
    # уникальные LineSource + сжатый массив индексов на строку.
    RESOLVED_DRAFT_VERSION = 2
    LINE_SOURCE_IDS_VERSION = 2

    def __init__(
        self,
        base_dir,
        asset_registry=None,
        file_readers=None,
    ):
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._project_roots = self._load_project_roots()
        self.active: Optional[Project] = None
        # Нельзя использовать ``or``: пустой реестр — валидный объект,
        # а ``ProjectAssetRegistry`` определяет ``__len__`` и поэтому
        # ложен, пока в нём нет типов.
        self.asset_registry = (
            asset_registry
            if asset_registry is not None
            else ProjectAssetRegistry()
        )
        self.file_readers = (
            file_readers
            if file_readers is not None
            else ProjectFileReaderRegistry()
        )
        self._preview_cache = OrderedDict()
        # Сериализует запись файлов черновиков: фоновая запись текста и
        # автосохранение не должны одновременно писать один файл.
        self._write_lock = threading.RLock()
        # Путь фактического JSON-файла resolved-черновика. Нужен для
        # миграции старых имён файлов: при сохранении перезаписываем
        # именно исходный файл, а не создаём рядом второй.
        self._resolved_draft_files = {}

    # -------------------------------------------------- свойства
    @property
    def is_active(self) -> bool:
        return self.active is not None

    def patch_mode(self) -> str:
        if self.active is None:
            return "root"
        return normalize_save_mode(self.active.save_mode or "root")

    def is_root_mode(self) -> bool:
        return self.patch_mode() == "root"

    def is_on_file_mode(self) -> bool:
        return self.patch_mode() == "on_file"

    @staticmethod
    def path_key(path) -> str:
        from sdk.paths import path_key

        return path_key(path)

    def _draft_filename(self, path) -> str:
        digest = hashlib.sha1(
            self.path_key(path).encode("utf-8", "replace")
        ).hexdigest()
        return digest + ".json"

    def _load_project_roots(self):
        roots = [self.base_dir]
        path = self.base_dir / self.ROOTS_NAME
        if not path.is_file():
            return roots
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return roots
        for value in data if isinstance(data, list) else ():
            try:
                root = Path(value).expanduser().resolve()
            except (OSError, TypeError, ValueError):
                continue
            if root not in roots:
                roots.append(root)
        return roots

    def _save_project_roots(self):
        data = [
            str(root)
            for root in self._project_roots
            if root != self.base_dir
        ]
        try:
            (self.base_dir / self.ROOTS_NAME).write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _register_project_root(self, root) -> None:
        try:
            root = Path(root).expanduser().resolve()
        except (OSError, TypeError, ValueError):
            return
        if root not in self._project_roots:
            self._project_roots.append(root)
            self._save_project_roots()

    def _project_dir(self, project_id: str) -> Path:
        for root in self._project_roots:
            candidate = root / project_id
            if (candidate / self.META_NAME).is_file():
                return candidate
        return self.base_dir / project_id

    def suggest_project_dir(self, name: str) -> Path:
        """Предложить каталог нового проекта по его названию."""
        return self.base_dir / self._make_project_id(name)

    # -------------------------------------------------- создание / загрузка
    def create(
        self,
        name: str,
        modules: Optional[Iterable[str]] = None,
        save_mode: str = "root",
        xml_patch_mode: str = "xml",
        project_dir=None,
    ) -> Project:
        name = (name or "").strip()
        if not name:
            raise ProjectError("Название проекта не может быть пустым")

        save_mode = normalize_save_mode(save_mode)
        xml_patch_mode = str(xml_patch_mode or "xml").lower()
        if xml_patch_mode not in ("dxml_lua", "xml", "xml_override"):
            raise ProjectError(
                "Неизвестный режим XML-патча: %s" % xml_patch_mode
            )

        if self.active is not None:
            self.close()

        self._preview_cache.clear()
        self._resolved_draft_files.clear()
        if project_dir:
            root_dir = Path(project_dir).expanduser()
            if not root_dir.is_absolute():
                raise ProjectError(
                    "Каталог проекта должен быть абсолютным: %s"
                    % root_dir
                )
            root_dir = root_dir.resolve()
            if root_dir.parent == root_dir:
                raise ProjectError(
                    "Нельзя сохранить проект в корень диска"
                )
            if root_dir.exists():
                if not root_dir.is_dir():
                    raise ProjectError(
                        "Каталог проекта не является папкой: %s"
                        % root_dir
                    )
                if any(root_dir.iterdir()):
                    raise ProjectError(
                        "Каталог проекта уже существует и не пуст: %s"
                        % root_dir
                    )
            else:
                try:
                    root_dir.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    raise ProjectError(
                        "Не удалось создать каталог проекта: %s" % exc
                    )
            project_id = root_dir.name
        else:
            project_id = self._make_project_id(name)
            root_dir = self._project_dir(project_id)
            root_dir.mkdir(parents=True, exist_ok=False)

        self._register_project_root(root_dir.parent)
        (root_dir / self.DRAFTS_DIR).mkdir(parents=True, exist_ok=True)
        (root_dir / self.RESOLVED_DIR).mkdir(parents=True, exist_ok=True)
        (root_dir / self.ASSETS_DIR).mkdir(parents=True, exist_ok=True)

        project = Project(
            id=project_id,
            name=name,
            root_dir=root_dir,
            modules=list(modules or []),
            save_mode=save_mode,
            xml_patch_mode=xml_patch_mode,
            created_at=self._timestamp(),
            drafts={},
        )
        self.active = project
        self._write_meta(project)
        return project

    def load(self, project_id: str) -> Project:
        root_dir = self._project_dir(project_id)
        meta_path = root_dir / self.META_NAME

        if not meta_path.is_file():
            raise ProjectError("Проект не найден: %s" % project_id)

        if self.active is not None:
            self.close()

        self._preview_cache.clear()
        self._resolved_draft_files.clear()
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ProjectError("Не удалось прочитать проект: %s" % exc)

        save_mode = normalize_save_mode(meta["save_mode"])
        xml_patch_mode = str(meta["xml_patch_mode"]).lower()
        if xml_patch_mode not in ("dxml_lua", "xml", "xml_override"):
            raise ProjectError("Неизвестный режим XML-патча: %s" % xml_patch_mode)

        module_settings = {}
        raw_settings = meta.get("module_settings") or {}
        if isinstance(raw_settings, dict):
            for key, value in raw_settings.items():
                if not isinstance(key, str) or not key.strip():
                    continue
                if value is None or isinstance(
                    value,
                    (str, bool, int, float),
                ):
                    module_settings[key] = value

        project = Project(
            id=str(meta["id"]),
            name=str(meta["name"]),
            root_dir=root_dir,
            modules=list(meta.get("modules") or []),
            save_mode=save_mode,
            xml_patch_mode=xml_patch_mode,
            created_at=str(meta.get("created_at") or ""),
            drafts={},
            resolved_drafts={},
            assets={},
            module_settings=module_settings,
        )

        drafts_dir = root_dir / self.DRAFTS_DIR
        if drafts_dir.is_dir():
            for draft_path in sorted(drafts_dir.glob("*.json")):
                draft = self._read_draft(draft_path)
                if draft is not None:
                    project.drafts[self.path_key(draft.path)] = draft

        resolved_dir = root_dir / self.RESOLVED_DIR
        if resolved_dir.is_dir():
            for draft_path in sorted(resolved_dir.glob("*.json")):
                draft = self._read_resolved_draft(draft_path)
                if draft is None:
                    continue
                key = self.resolved_key(draft.tab_id, draft.root_path)
                previous = project.resolved_drafts.get(key)
                # В старых проектах имя файла не совпадало с ключом,
                # поэтому после миграции могли остаться два файла на один
                # черновик. Выбираем более свежий по saved_at.
                if previous is not None and previous.saved_at > draft.saved_at:
                    continue
                project.resolved_drafts[key] = draft
                self._resolved_draft_files[key] = draft_path

        assets_dir = root_dir / self.ASSETS_DIR
        if assets_dir.is_dir():
            for draft_path in sorted(assets_dir.glob("*.json")):
                draft = self._read_asset_draft(draft_path)
                if draft is not None:
                    project.assets[self.path_key(draft.path)] = draft

        self.active = project
        return project

    def list_projects(self) -> List[ProjectInfo]:
        result: List[ProjectInfo] = []
        seen = set()

        entries = []
        for root in self._project_roots:
            if not root.is_dir():
                continue
            for entry in root.iterdir():
                if not entry.is_dir():
                    continue
                try:
                    key = entry.resolve()
                except OSError:
                    key = entry
                if key in seen:
                    continue
                seen.add(key)
                entries.append(entry)

        for entry in sorted(entries, key=lambda item: str(item).lower()):
            meta_path = entry / self.META_NAME
            if not meta_path.is_file():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                continue

            drafts_dir = entry / self.DRAFTS_DIR
            resolved_dir = entry / self.RESOLVED_DIR
            assets_dir = entry / self.ASSETS_DIR
            draft_count = 0
            if drafts_dir.is_dir():
                draft_count += len(list(drafts_dir.glob("*.json")))
            if resolved_dir.is_dir():
                draft_count += len(list(resolved_dir.glob("*.json")))
            if assets_dir.is_dir():
                draft_count += len(list(assets_dir.glob("*.json")))

            try:
                save_mode = normalize_save_mode(meta["save_mode"])
                xml_patch_mode = str(meta["xml_patch_mode"]).lower()
                if xml_patch_mode not in ("dxml_lua", "xml", "xml_override"):
                    continue
            except (KeyError, TypeError, ProjectError):
                continue

            result.append(
                ProjectInfo(
                    id=str(meta["id"]),
                    name=str(meta["name"]),
                    root_dir=entry,
                    created_at=str(meta.get("created_at") or ""),
                    draft_count=draft_count,
                    save_mode=save_mode,
                    xml_patch_mode=xml_patch_mode,
                )
            )

        return result

    def close(self) -> None:
        self._preview_cache.clear()
        if self.active is None:
            return
        self.flush()
        self.active = None
        self._resolved_draft_files.clear()

    def set_patch_mode(self, mode: str) -> Project:
        """Переключить режим формирования патча у активного проекта."""
        project = self._require_active()
        mode = normalize_save_mode(mode)

        project.save_mode = mode
        self._preview_cache.clear()
        self._write_meta(project)
        return project

    def set_xml_patch_mode(self, mode: str) -> Project:
        """Переключить способ формирования XML-патча."""
        project = self._require_active()
        mode = str(mode or "xml").lower()
        if mode not in ("dxml_lua", "xml", "xml_override"):
            raise ProjectError("Неизвестный режим XML-патча: %s" % mode)

        project.xml_patch_mode = mode
        self._preview_cache.clear()
        self._write_meta(project)
        return project

    def module_setting(self, key, default=None):
        """Текущее значение настройки модуля у активного проекта."""
        if self.active is None:
            return default
        return self.active.module_setting(key, default)

    def module_settings(self) -> Dict[str, object]:
        """Копия всех сохранённых настроек модулей активного проекта."""
        if self.active is None:
            return {}
        return dict(self.active.module_settings)

    def set_module_setting(self, key, value):
        """Сохранить значение настройки модуля в активном проекте."""
        project = self._require_active()
        key = str(key or "").strip()
        if not key:
            raise ProjectError("Ключ настройки проекта не может быть пустым")

        project.module_settings[key] = value
        self._preview_cache.clear()
        self._write_meta(project)
        return value

    def delete_active_project(self) -> bool:
        """Удалить активный проект со всеми черновиками.

        Это разрушительная операция: проект не сохраняется и не переносится
        в корзину. Каталог удаляется только если он находится внутри
        хранилища проектов.
        """
        project = self._require_active()
        root = Path(project.root_dir)
        target = root.resolve()

        inside = False
        for allowed in self._project_roots:
            allowed_path = allowed.resolve()
            if target == allowed_path:
                continue
            try:
                common = os.path.commonpath(
                    [str(target), str(allowed_path)]
                )
            except (ValueError, OSError):
                continue
            if os.path.normcase(common) == os.path.normcase(
                str(allowed_path)
            ):
                inside = True
                break

        if not inside:
            raise ProjectError(
                "Отказ: каталог проекта находится вне известных "
                "каталогов проектов"
            )

        self._preview_cache.clear()

        if not target.exists():
            self.active = None
            self._resolved_draft_files.clear()
            return True

        try:
            shutil.rmtree(str(target))
        except OSError as exc:
            raise ProjectError("Не удалось удалить проект: %s" % exc)

        self.active = None
        self._resolved_draft_files.clear()
        return True

    # -------------------------------------------------- черновики
    def update_draft(
        self,
        path,
        text: str,
        original_text=None,
    ) -> ProjectDraft:
        # Правка и запись одного черновика сериализуются одним замком:
        # фоновое сохранение не должно записать устаревший снимок после
        # более новой правки.
        with self._write_lock:
            return self._update_draft_locked(
                path,
                text,
                original_text=original_text,
            )

    def _update_draft_locked(
        self,
        path,
        text: str,
        original_text=None,
    ) -> ProjectDraft:
        project = self._require_active()
        path = Path(path)
        key = self.path_key(path)
        draft = project.drafts.get(key)

        new_text = str(text or "")

        if draft is None:
            if original_text is not None:
                original = str(original_text)
            elif path.is_file():
                original = (
                    read_xml_text(path)
                    if path.suffix.lower() == ".xml"
                    else read_text_safe(path)
                )
            else:
                original = ""
            draft = ProjectDraft(
                path=path,
                original_text=original,
                current_text=new_text,
                saved_text=original,
                is_new=not path.is_file(),
                revision=0,
                updated_at=time.time(),
                saved_at=0.0,
            )
            project.drafts[key] = draft
        elif draft.current_text != new_text:
            draft.current_text = new_text
            draft.revision += 1
            draft.updated_at = time.time()
            self._invalidate_preview_cache(path)

        return draft

    def save_draft(self, path, text: Optional[str] = None) -> ProjectDraft:
        project = self._require_active()
        draft = self.update_draft(path, text if text is not None else self.get_text(path) or "")
        draft.saved_text = draft.current_text
        draft.saved_at = time.time()
        self._write_draft(project, draft)
        return draft

    def stage_text_draft(
        self,
        path,
        text: str,
        original_text=None,
    ):
        """Подготовить черновик к фоновой записи.

        Возвращает ``(target, payload, revision, saved_at)``. Изменения
        в памяти применяются сразу, а тяжёлая сериализация и запись
        выполняются в отдельном потоке через :meth:`write_draft_payload`.
        """
        with self._write_lock:
            project = self._require_active()
            draft = self._update_draft_locked(
                path,
                text,
                original_text=original_text,
            )
            saved_at = time.time()
            payload = {
                "path": str(draft.path),
                "original_text": draft.original_text,
                "current_text": draft.current_text,
                "saved_text": draft.current_text,
                "is_new": bool(draft.is_new),
                "revision": int(draft.revision),
                "updated_at": draft.updated_at,
                "saved_at": saved_at,
            }
            target = (
                project.root_dir
                / self.DRAFTS_DIR
                / self._draft_filename(draft.path)
            )
            return target, payload, draft.revision, saved_at

    def write_draft_payload(self, target, payload) -> None:
        """Записать заранее подготовленный JSON черновика (потокобезопасно)."""
        with self._write_lock:
            self._write_payload_locked(target, payload)

    def write_staged_draft(self, path, target, payload, revision) -> bool:
        """Записать снимок, если черновик не изменился с момента подготовки.

        Проверка и запись выполняются под одним замком: автосохранение
        или новая правка не могут вклиниться и записать устаревший текст
        поверх свежего.
        """
        with self._write_lock:
            draft = self.get_draft(path)
            if (
                draft is not None
                and int(draft.revision) != int(revision)
            ):
                return False
            self._write_payload_locked(target, payload)
            return True

    def _write_payload_locked(self, target, payload) -> None:
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(
            target.name
            + ".tmp.%d.%d" % (os.getpid(), threading.get_ident())
        )
        try:
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(str(tmp), str(target))
        except Exception:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise

    def mark_draft_saved(self, path, revision, saved_at=None) -> bool:
        """Зафиксировать успешную фоновую запись в памяти проекта."""
        draft = self.get_draft(path)
        if draft is None or int(draft.revision) != int(revision):
            return False
        draft.saved_text = draft.current_text
        draft.saved_at = (
            time.time() if saved_at is None else float(saved_at)
        )
        return True

    def restore_draft(self, draft: ProjectDraft) -> ProjectDraft:
        """Вернуть снимок черновика (откат транзакции)."""
        project = self._require_active()
        project.drafts[self.path_key(draft.path)] = draft
        self._invalidate_preview_cache(draft.path)
        self._write_draft(project, draft)
        return draft

    def save_text(self, path, text: str) -> ProjectDraft:
        """Сохранить конкретный текст как текущую версию черновика."""
        return self.save_draft(path, text)

    def autosave(self) -> List[SavedDraft]:
        project = self._require_active()
        saved: List[SavedDraft] = []

        for draft in project.drafts.values():
            if not draft.is_dirty:
                continue
            draft.saved_text = draft.current_text
            draft.saved_at = time.time()
            self._write_draft(project, draft)
            saved.append(
                SavedDraft(
                    kind="file",
                    key=self.path_key(draft.path),
                    path=draft.path,
                    text=draft.current_text,
                )
            )

        for draft in project.assets.values():
            if not draft.is_dirty:
                continue
            draft.saved_bytes = draft.current_bytes
            draft.saved_at = time.time()
            self._write_asset_draft(project, draft)
            saved.append(
                SavedDraft(
                    kind="asset",
                    key=self.path_key(draft.path),
                    path=draft.path,
                    text="",
                )
            )

        for draft in project.resolved_drafts.values():
            if not draft.is_dirty:
                continue
            draft.saved_text = draft.current_text
            draft.saved_at = time.time()
            self._write_resolved_draft(project, draft)
            saved.append(
                SavedDraft(
                    kind="resolved",
                    key=self.resolved_key(draft.tab_id, draft.root_path),
                    path=draft.root_path,
                    text=draft.current_text,
                )
            )

        return saved

    def flush(self) -> List[SavedDraft]:
        if self.active is None:
            return []
        return self.autosave()

    def get_text(self, path) -> Optional[str]:
        if self.active is None:
            return None
        draft = self.active.draft(path)
        return draft.current_text if draft is not None else None

    def get_draft(self, path) -> Optional[ProjectDraft]:
        if self.active is None:
            return None
        return self.active.draft(path)

    def remove_draft(self, path) -> bool:
        project = self._require_active()
        key = self.path_key(path)
        draft = project.drafts.pop(key, None)
        if draft is None:
            return False

        self._invalidate_preview_cache(path)
        draft_file = project.root_dir / self.DRAFTS_DIR / self._draft_filename(path)
        try:
            if draft_file.is_file():
                draft_file.unlink()
        except OSError:
            pass
        return True

    def changed_paths(self) -> List[Path]:
        """Файлы, изменения которых влияют на резолв."""
        paths = []
        for draft in self.changed_drafts():
            paths.append(Path(draft.path))
        for draft in self.changed_resolved_drafts():
            paths.append(Path(draft.root_path))
        return paths

    def changed_drafts(self) -> List[ProjectDraft]:
        if self.active is None:
            return []
        return [
            draft
            for draft in self.active.drafts.values()
            if draft.has_changes
        ]

    def dirty_drafts(self) -> List[ProjectDraft]:
        if self.active is None:
            return []
        return [
            draft
            for draft in self.active.drafts.values()
            if draft.is_dirty
        ]

    # -------------------------------------------------- двоичные черновики
    @staticmethod
    def _asset_draft_filename(path) -> str:
        digest = hashlib.sha1(
            ProjectService.path_key(path).encode("utf-8", "replace")
        ).hexdigest()
        return digest + ".json"

    def update_asset_draft(
        self,
        path,
        data: bytes,
        is_new: Optional[bool] = None,
    ) -> ProjectAssetDraft:
        """Обновить двоичный черновик в памяти (без записи в проект)."""
        project = self._require_active()
        path = Path(path)
        key = self.path_key(path)
        draft = project.assets.get(key)
        payload = bytes(data or b"")

        if draft is None:
            if path.is_file():
                try:
                    original = path.read_bytes()
                except OSError:
                    original = b""
                original_sha1 = hashlib.sha1(original).hexdigest()
                saved = original
                new_flag = False
            else:
                original_sha1 = ""
                saved = b""
                new_flag = True

            if is_new is not None:
                new_flag = bool(is_new)

            draft = ProjectAssetDraft(
                path=path,
                original_sha1=original_sha1,
                current_bytes=payload,
                saved_bytes=saved,
                is_new=new_flag,
                revision=0,
                updated_at=time.time(),
                saved_at=0.0,
            )
            project.assets[key] = draft
        elif draft.current_bytes != payload:
            draft.current_bytes = payload
            draft.revision += 1
            draft.updated_at = time.time()
            if is_new is not None:
                draft.is_new = bool(is_new)
            self._invalidate_preview_cache(path)

        return draft

    def save_asset_draft(
        self,
        path,
        data: Optional[bytes] = None,
    ) -> ProjectAssetDraft:
        project = self._require_active()
        draft = project.assets.get(self.path_key(path))
        if draft is None:
            if data is None:
                raise ProjectError("Двоичный черновик не найден: %s" % path)
            draft = self.update_asset_draft(path, data)
        elif data is not None:
            draft = self.update_asset_draft(path, data)

        draft.saved_bytes = draft.current_bytes
        draft.saved_at = time.time()
        self._write_asset_draft(project, draft)
        return draft

    def get_asset_draft(self, path) -> Optional[ProjectAssetDraft]:
        if self.active is None:
            return None
        return self.active.assets.get(self.path_key(path))

    def get_asset_bytes(self, path) -> Optional[bytes]:
        draft = self.get_asset_draft(path)
        return draft.current_bytes if draft is not None else None

    def remove_asset_draft(self, path) -> bool:
        project = self._require_active()
        key = self.path_key(path)
        draft = project.assets.pop(key, None)
        if draft is None:
            return False

        self._invalidate_preview_cache(path)
        target = (
            project.root_dir
            / self.ASSETS_DIR
            / self._asset_draft_filename(path)
        )
        try:
            if target.is_file():
                target.unlink()
        except OSError:
            pass
        return True

    def changed_asset_drafts(self) -> List[ProjectAssetDraft]:
        if self.active is None:
            return []
        return [
            draft
            for draft in self.active.assets.values()
            if draft.has_changes
        ]

    def dirty_asset_drafts(self) -> List[ProjectAssetDraft]:
        if self.active is None:
            return []
        return [
            draft
            for draft in self.active.assets.values()
            if draft.is_dirty
        ]

    # -------------------------------------------------- resolved-черновики
    @staticmethod
    def resolved_key(tab_id, root_path) -> str:
        return resolved_key(tab_id, root_path)

    def update_resolved_draft(
        self,
        tab_id,
        root_path,
        title,
        original_text,
        current_text,
        line_sources=None,
    ) -> ProjectResolvedDraft:
        project = self._require_active()
        key = self.resolved_key(tab_id, root_path)
        draft = project.resolved_drafts.get(key)
        original_text = str(original_text or "")
        raw_current = str(current_text or "")
        current_text = self._collapse_duplicated_text(
            raw_current,
            original_text,
        )
        if current_text != raw_current:
            logging.getLogger(__name__).warning(
                "Resolved-черновик %s получил двойной текст; "
                "оставлена одна половина.\n%s",
                tab_id,
                "".join(traceback.format_stack(limit=12)),
            )

        if draft is None:
            draft = ProjectResolvedDraft(
                root_path=Path(root_path),
                title=str(title or ""),
                original_text=original_text,
                current_text=current_text,
                saved_text=original_text,
                tab_id=str(tab_id or ""),
                line_sources=tuple(line_sources or ()),
                updated_at=time.time(),
            )
            project.resolved_drafts[key] = draft
        else:
            draft.current_text = current_text
            draft.title = str(title or draft.title)
            draft.updated_at = time.time()
            if line_sources is not None:
                draft.line_sources = tuple(line_sources)

        return draft

    def save_resolved_draft(
        self,
        tab_id,
        text: Optional[str] = None,
    ) -> ProjectResolvedDraft:
        project = self._require_active()
        key = self.resolved_key(tab_id, "")
        draft = project.resolved_drafts.get(key)

        if draft is None:
            raise ProjectError("Resolved-черновик не найден: %s" % tab_id)

        if text is not None:
            draft.current_text = self._collapse_duplicated_text(
                str(text),
                draft.original_text,
            )

        draft.saved_text = draft.current_text
        draft.saved_at = time.time()
        self._write_resolved_draft(project, draft)
        return draft

    def get_resolved_text(self, tab_id) -> Optional[str]:
        draft = self.get_resolved_draft(tab_id)
        return draft.current_text if draft is not None else None

    def get_resolved_draft(self, tab_id) -> Optional[ProjectResolvedDraft]:
        if self.active is None:
            return None
        return self.active.resolved_drafts.get(str(tab_id))

    def remove_resolved_draft(self, tab_id) -> bool:
        project = self._require_active()
        key = str(tab_id)
        draft = project.resolved_drafts.pop(key, None)
        if draft is None:
            return False

        target = self._resolved_draft_files.pop(key, None)
        if target is None:
            target = (
                project.root_dir
                / self.RESOLVED_DIR
                / self._resolved_draft_filename(draft)
            )
        try:
            if target.is_file():
                target.unlink()
        except OSError:
            pass
        return True

    def changed_resolved_drafts(self) -> List[ProjectResolvedDraft]:
        if self.active is None:
            return []
        return [
            draft
            for draft in self.active.resolved_drafts.values()
            if draft.has_changes
        ]

    def dirty_resolved_drafts(self) -> List[ProjectResolvedDraft]:
        if self.active is None:
            return []
        return [
            draft
            for draft in self.active.resolved_drafts.values()
            if draft.is_dirty
        ]

    # -------------------------------------------------- статус
    def has_changes(self) -> bool:
        """Есть ли изменения относительно исходных файлов."""
        return bool(
            self.changed_drafts()
            or self.changed_resolved_drafts()
            or self.changed_asset_drafts()
        )

    def has_dirty(self) -> bool:
        """Есть ли изменения, ещё не записанные в каталог проекта."""
        return bool(
            self.dirty_drafts()
            or self.dirty_resolved_drafts()
            or self.dirty_asset_drafts()
        )

    def status_text(self) -> str:
        if self.active is None:
            return "Проект: не создан"
        changed = (
            len(self.changed_drafts())
            + len(self.changed_resolved_drafts())
            + len(self.changed_asset_drafts())
        )
        dirty = (
            len(self.dirty_drafts())
            + len(self.dirty_resolved_drafts())
            + len(self.dirty_asset_drafts())
        )
        mode = self.patch_mode()
        return (
            "Проект: %s | режим: %s | XML: %s | изменённых файлов: %d | "
            "несохранённых: %d"
            % (
                self.active.name,
                mode,
                self.active.xml_patch_mode,
                changed,
                dirty,
            )
        )

    # -------------------------------------------------- кэш предпросмотра
    def _require_active(self) -> Project:
        if self.active is None:
            raise ProjectError("Проект не создан")
        return self.active

    def _write_meta(self, project: Project) -> None:
        project.root_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "id": project.id,
            "name": project.name,
            "created_at": project.created_at,
            "modules": list(project.modules),
            "save_mode": project.save_mode,
            "xml_patch_mode": project.xml_patch_mode,
            "module_settings": dict(project.module_settings),
        }
        (project.root_dir / self.META_NAME).write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _write_draft(self, project: Project, draft: ProjectDraft) -> None:
        drafts_dir = project.root_dir / self.DRAFTS_DIR
        drafts_dir.mkdir(parents=True, exist_ok=True)

        data = {
            "path": str(draft.path),
            "original_text": draft.original_text,
            "current_text": draft.current_text,
            "saved_text": draft.saved_text,
            "is_new": bool(draft.is_new),
            "revision": int(draft.revision),
            "updated_at": draft.updated_at,
            "saved_at": draft.saved_at,
        }
        target = drafts_dir / self._draft_filename(draft.path)
        with self._write_lock:
            target.write_text(
                json.dumps(data, ensure_ascii=False),
                encoding="utf-8",
            )

    def _write_asset_draft(
        self,
        project: Project,
        draft: ProjectAssetDraft,
    ) -> None:
        assets_dir = project.root_dir / self.ASSETS_DIR
        assets_dir.mkdir(parents=True, exist_ok=True)

        data = {
            "path": str(draft.path),
            "original_sha1": draft.original_sha1,
            "current_b64": base64.b64encode(draft.current_bytes).decode(
                "ascii"
            ),
            "saved_b64": base64.b64encode(draft.saved_bytes).decode("ascii"),
            "is_new": bool(draft.is_new),
            "revision": int(draft.revision),
            "updated_at": draft.updated_at,
            "saved_at": draft.saved_at,
        }
        target = assets_dir / self._asset_draft_filename(draft.path)
        with self._write_lock:
            target.write_text(
                json.dumps(data, ensure_ascii=False),
                encoding="utf-8",
            )

    def _read_asset_draft(
        self,
        draft_path: Path,
    ) -> Optional[ProjectAssetDraft]:
        try:
            data = json.loads(draft_path.read_text(encoding="utf-8"))
            return ProjectAssetDraft(
                path=Path(data["path"]),
                original_sha1=str(data.get("original_sha1") or ""),
                current_bytes=base64.b64decode(
                    data.get("current_b64") or ""
                ),
                saved_bytes=base64.b64decode(
                    data.get("saved_b64") or ""
                ),
                is_new=bool(data.get("is_new")),
                revision=int(data.get("revision") or 0),
                updated_at=float(data.get("updated_at") or 0.0),
                saved_at=float(data.get("saved_at") or 0.0),
            )
        except Exception:
            return None

    def _resolved_draft_filename(self, draft: ProjectResolvedDraft) -> str:
        digest = hashlib.sha1(
            self.resolved_key(draft.tab_id, draft.root_path).encode(
                "utf-8",
                "replace",
            )
        ).hexdigest()
        return digest + ".json"

    def _write_resolved_draft(
        self,
        project: Project,
        draft: ProjectResolvedDraft,
    ) -> None:
        resolved_dir = project.root_dir / self.RESOLVED_DIR
        resolved_dir.mkdir(parents=True, exist_ok=True)

        # Не даём записать уже испорченный «двойной» текст.
        compact = self._collapse_duplicated_text(
            draft.current_text,
            draft.original_text,
        )
        if compact != draft.current_text:
            draft.current_text = compact
            if draft.saved_text == compact + compact:
                draft.saved_text = compact

        data = {
            "version": self.RESOLVED_DRAFT_VERSION,
            "tab_id": draft.tab_id,
            "root_path": str(draft.root_path),
            "title": draft.title,
            "original_text": draft.original_text,
            "current_text": draft.current_text,
            "line_sources": self._serialize_line_sources(
                draft.line_sources
            ),
            "updated_at": draft.updated_at,
            "saved_at": draft.saved_at,
        }
        # Обычный случай после автосохранения: saved == current. Не храним
        # вторую 40-МБ копию текста; при чтении восстановим её из current.
        if draft.saved_text != draft.current_text:
            data["saved_text"] = draft.saved_text

        key = self.resolved_key(draft.tab_id, draft.root_path)
        target = self._resolved_draft_files.get(key)
        if target is None:
            target = resolved_dir / self._resolved_draft_filename(draft)
        self._write_json_atomic(target, data)
        self._resolved_draft_files[key] = target

    def _read_resolved_draft(
        self,
        draft_path: Path,
    ) -> Optional[ProjectResolvedDraft]:
        try:
            data = json.loads(draft_path.read_text(encoding="utf-8"))
            original_text = self._read_text_field(data, "original_text")
            raw_current = self._read_text_field(data, "current_text")
            current_text = self._collapse_duplicated_text(
                raw_current,
                original_text,
            )
            saved_text = self._read_text_field(
                data,
                "saved_text",
                default=raw_current,
            )
            if saved_text == raw_current:
                saved_text = current_text
            else:
                saved_text = self._collapse_duplicated_text(
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
                line_sources=self._deserialize_line_sources(
                    data.get("line_sources")
                ),
                updated_at=float(data.get("updated_at") or 0.0),
                saved_at=float(data.get("saved_at") or 0.0),
            )
        except Exception:
            return None

    @staticmethod
    def _read_text_field(data, key, default=""):
        """Прочитать текстовое поле старого или нового формата.

        Пока поддерживается только plain text. Отдельный helper оставлен,
        чтобы позже можно было добавить сжатие без размазывания условия
        по всем местам чтения.
        """
        if key not in data:
            return default
        value = data.get(key)
        return str(value if value is not None else "")

    @staticmethod
    def _collapse_duplicated_text(text, original=""):
        """Починить старый дефект: current = current + current.

        Это защита для уже испорченных проектов. Признак — ровно две
        одинаковые половины и вторая половина близка по длине к
        original_text.
        """
        return collapse_duplicated_text(text, original)

    @staticmethod
    def _line_source_record(source):
        path = getattr(source, "path", None)
        return {
            "kind": str(getattr(source, "kind", "") or ""),
            "display": str(getattr(source, "display", "") or ""),
            "path": str(path) if path is not None else "",
            "line": int(getattr(source, "line", 0) or 0),
            "open_raw": bool(getattr(source, "open_raw", False)),
        }

    @staticmethod
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

    @staticmethod
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

    @classmethod
    def _serialize_line_sources(cls, line_sources) -> dict:
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

            record = cls._line_source_record(source)
            key = cls._line_source_key(record)
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
            "version": cls.LINE_SOURCE_IDS_VERSION,
            "sources": records,
            "ids": base64.b64encode(packed).decode("ascii"),
        }

    @classmethod
    def _deserialize_line_sources(cls, values) -> tuple:
        """Прочитать новый компактный формат или старый список объектов."""
        if isinstance(values, dict):
            return cls._deserialize_line_sources_v2(values)

        cache = {}
        result = []
        for data in values or ():
            if not isinstance(data, dict):
                # None — валидная запись «источник неизвестен». Нельзя
                # выбрасывать её: иначе индексы строк сдвинутся.
                result.append(None)
                continue
            key = cls._line_source_key(data)
            source = cache.get(key)
            if source is None:
                source = cls._line_source_from_record(data)
                if key is not None:
                    cache[key] = source
            result.append(source)
        return tuple(result)

    @classmethod
    def _deserialize_line_sources_v2(cls, values) -> tuple:
        records = values.get("sources") or ()
        parsed = tuple(
            cls._line_source_from_record(record)
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

    @staticmethod
    def _write_json_atomic(target: Path, data) -> None:
        """Записать JSON через временный файл и os.replace."""
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(
            target.name + ".tmp.%d" % os.getpid()
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

    def _read_draft(self, draft_path: Path) -> Optional[ProjectDraft]:
        try:
            data = json.loads(draft_path.read_text(encoding="utf-8"))
            path = Path(data["path"])
            return ProjectDraft(
                path=path,
                original_text=str(data["original_text"]),
                current_text=str(data["current_text"]),
                saved_text=str(data["saved_text"]),
                is_new=bool(data["is_new"]),
                revision=int(data["revision"]),
                updated_at=float(data.get("updated_at") or 0.0),
                saved_at=float(data.get("saved_at") or 0.0),
            )
        except Exception:
            return None

    def _make_project_id(self, name: str) -> str:
        slug = re.sub(r"[^\w.-]+", "_", name.strip().lower(), flags=re.UNICODE)
        slug = slug.strip("._-") or "project"
        base = "%s_%s" % (slug, time.strftime("%Y%m%d_%H%M%S"))
        candidate = base
        index = 2
        while (self.base_dir / candidate).exists():
            candidate = "%s_%d" % (base, index)
            index += 1
        return candidate

    @staticmethod
    def _timestamp() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S")
