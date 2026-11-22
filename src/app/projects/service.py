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

import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
import traceback
from collections import OrderedDict
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from core.api.documents import analyze_multiline_edit
from core.api.patches import (
    GeneratedPatch,
    PatchFile,
    PatchIssue,
    PatchOptions,
    PatchPlan,
    PatchTarget,
    VerificationCheck,
    VerificationResult,
    merge_patch_plans,
)
from core.api.vfs import read_text_safe, read_xml_text

from sdk.contracts import ProjectOverlay
from sdk.features.projects import SavedDraft

from app.documents import collapse_duplicated_text

from .assets import (
    ProjectAssetRegistry,
    ProjectAssetTypeError,
)
from .readers import FileReaderError, ProjectFileReaderRegistry
from .drafts import DraftStore
from .session import ProjectSession
from .repository import (
    ASSETS_DIR as PROJECT_ASSETS_DIR,
    DRAFTS_DIR as PROJECT_DRAFTS_DIR,
    META_NAME as PROJECT_META_NAME,
    RESOLVED_DIR as PROJECT_RESOLVED_DIR,
    TABS_NAME as PROJECT_TABS_NAME,
    ProjectRepository,
    write_json_atomic,
)


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

log = logging.getLogger(__name__)

# Отличает отсутствующий ключ настройки от сохранённого значения None.
_MISSING_SETTING = object()


class ProjectService(ProjectPreviewMixin):
    """Хранилище проектов и черновиков."""

    META_NAME = PROJECT_META_NAME
    DRAFTS_DIR = PROJECT_DRAFTS_DIR
    RESOLVED_DIR = PROJECT_RESOLVED_DIR
    ASSETS_DIR = PROJECT_ASSETS_DIR
    ROOTS_NAME = "project_roots.json"
    # Сессия редактора: какие вкладки были открыты при прошлом закрытии.
    TABS_NAME = PROJECT_TABS_NAME
    TABS_VERSION = 1

    PREVIEW_CACHE_SIZE = 128
    # Кэш предпросмотра ограничен и по объёму: GeneratedPatch хранит
    # полные тексты файлов, а не только план.
    PREVIEW_CACHE_MAX_CHARS = 32_000_000

    def __init__(
        self,
        base_dir,
        asset_registry=None,
        file_readers=None,
    ):
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.repository = ProjectRepository()
        self.session = ProjectSession()
        self._project_roots = self._load_project_roots()
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
        self._preview_cache_sizes = {}
        self._preview_cache_chars = 0
        self._preview_lock = threading.RLock()
        # Сериализует запись файлов черновиков: фоновая запись текста и
        # автосохранение не должны одновременно писать один файл.
        self._write_lock = threading.RLock()
        self.draft_store = DraftStore(self._write_lock)
        # Защищает словари проекта и кэш overlay от чтения во время
        # фонового предпросмотра/автосохранения.
        self._state_lock = threading.RLock()
        self._overlay_cache = None
        self._overlay_cache_key = None
        # Путь фактического JSON-файла resolved-черновика. Нужен для
        # миграции старых имён файлов: при сохранении перезаписываем
        # именно исходный файл, а не создаём рядом второй.
        self._resolved_draft_files = {}

    # -------------------------------------------------- свойства
    @property
    def active(self) -> Optional[Project]:
        """Активный проект. Меняется только через ``ProjectSession``."""
        return self.session.active

    @property
    def is_active(self) -> bool:
        return self.session.is_active

    def _clear_overlay_cache(self):
        with self._state_lock:
            self._overlay_cache = None
            self._overlay_cache_key = None

    def _overlay_state_key(self):
        project = self.active
        if project is None:
            return None
        return (
            str(project.id),
            tuple(
                (
                    self.path_key(draft.path),
                    int(draft.revision),
                    bool(draft.is_new),
                )
                for draft in sorted(
                    project.drafts.values(),
                    key=lambda item: str(item.path).lower(),
                )
            ),
            tuple(
                (
                    self.path_key(draft.root_path),
                    str(draft.tab_id),
                    int(draft.revision),
                )
                for draft in sorted(
                    project.resolved_drafts.values(),
                    key=lambda item: str(item.root_path).lower(),
                )
            ),
        )

    def project_overlay(self) -> ProjectOverlay:
        """Снимок правок проекта. Собирается один раз на состояние."""
        with self._state_lock:
            project = self.active
            if project is None:
                return ProjectOverlay()

            key = self._overlay_state_key()
            if self._overlay_cache is not None and key == self._overlay_cache_key:
                return self._overlay_cache

            source_files = {}
            new_files = set()
            for draft in project.drafts.values():
                path = Path(draft.path)
                source_files[path] = str(draft.current_text or "")
                if draft.is_new:
                    new_files.add(path)

            resolved_edits = {}
            for draft in project.resolved_drafts.values():
                resolved_edits[Path(draft.root_path)] = (
                    str(draft.original_text or ""),
                    str(draft.current_text or ""),
                    tuple(draft.line_sources or ()),
                )

            revision_token = (
                hashlib.sha1(
                    repr(key).encode("utf-8", "replace")
                ).hexdigest()
                if key is not None
                else ""
            )
            overlay = ProjectOverlay(
                source_files=source_files,
                resolved_edits=resolved_edits,
                new_files=frozenset(new_files),
                revision_token=revision_token,
            )
            self._overlay_cache = overlay
            self._overlay_cache_key = key
            return overlay

    def patch_mode(self) -> str:
        state = self.session.state()
        if state is None:
            return "root"
        return normalize_save_mode(state.save_mode or "root")

    def is_root_mode(self) -> bool:
        return self.patch_mode() == "root"

    def is_on_file_mode(self) -> bool:
        return self.patch_mode() == "on_file"

    @staticmethod
    def path_key(path) -> str:
        from core.api.paths import path_key

        return path_key(path)

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

        self.invalidate_preview_cache()
        self._resolved_draft_files.clear()
        self._clear_overlay_cache()
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
        # Сначала фиксируем проект на диске: при ошибке записи активным
        # он не становится.
        self.repository.write_meta(project)
        self.session.activate(project)
        return project

    def load(self, project_id: str) -> Project:
        root_dir = self._project_dir(project_id)
        meta = self.repository.read_meta(root_dir)
        self.repository.validate_meta(meta)

        save_mode = normalize_save_mode(meta["save_mode"])
        xml_patch_mode = str(meta["xml_patch_mode"]).lower()
        if xml_patch_mode not in ("dxml_lua", "xml", "xml_override"):
            raise ProjectError("Неизвестный режим XML-патча: %s" % xml_patch_mode)
        # Схема переводится только после проверки: повреждённый или
        # чужой проект не перезаписывается. Ошибка чтения, проверки или
        # миграции не закрывает уже активный проект.
        meta = self.repository.ensure_current(root_dir, meta)

        if self.active is not None:
            self.close()

        self.invalidate_preview_cache()
        self._resolved_draft_files.clear()
        self._clear_overlay_cache()

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

        for draft in self.draft_store.read_text_drafts(root_dir):
            project.drafts[self.path_key(draft.path)] = draft

        for draft_path, draft in self.draft_store.read_resolved_drafts(
            root_dir
        ):
            key = self.resolved_key(draft.tab_id, draft.root_path)
            previous = project.resolved_drafts.get(key)
            # В старых проектах имя файла не совпадало с ключом,
            # поэтому после миграции могли остаться два файла на один
            # черновик. Выбираем более свежий по saved_at.
            if previous is not None and previous.saved_at > draft.saved_at:
                continue
            project.resolved_drafts[key] = draft
            self._resolved_draft_files[key] = draft_path

        for draft in self.draft_store.read_asset_drafts(root_dir):
            project.assets[self.path_key(draft.path)] = draft

        self.session.activate(project)
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
            try:
                meta = self.repository.read_meta(entry)
            except ProjectError:
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
                self.repository.validate_meta(meta)
                project_id = str(meta["id"])
                project_name = str(meta["name"])
                save_mode = normalize_save_mode(meta["save_mode"])
                xml_patch_mode = str(meta["xml_patch_mode"]).lower()
                if xml_patch_mode not in ("dxml_lua", "xml", "xml_override"):
                    continue
            except (KeyError, TypeError, ProjectError) as exc:
                log.warning(
                    "Проект: пропущен %s: %s",
                    entry / self.META_NAME,
                    exc,
                )
                continue

            result.append(
                ProjectInfo(
                    id=project_id,
                    name=project_name,
                    root_dir=entry,
                    created_at=str(meta.get("created_at") or ""),
                    draft_count=draft_count,
                    save_mode=save_mode,
                    xml_patch_mode=xml_patch_mode,
                )
            )

        return result

    def close(self) -> None:
        if self.active is None:
            self.invalidate_preview_cache()
            self._clear_overlay_cache()
            return
        try:
            self.flush()
        except Exception as exc:
            # Не теряем проект молча: вызывающая сторона должна показать
            # ошибку и оставить текущий проект активным.
            raise ProjectError(
                "Не удалось сохранить проект перед закрытием: %s" % exc
            ) from exc
        self.invalidate_preview_cache()
        self.session.deactivate()
        self._resolved_draft_files.clear()
        self._clear_overlay_cache()

    def set_patch_mode(self, mode: str) -> Project:
        """Переключить режим формирования патча у активного проекта."""
        mode = normalize_save_mode(mode)

        def command(project):
            previous = project.save_mode
            project.save_mode = mode
            try:
                self.repository.write_meta(project)
            except Exception:
                project.save_mode = previous
                raise
            return project

        project = self.session.write(command)
        self.invalidate_preview_cache()
        return project

    def set_xml_patch_mode(self, mode: str) -> Project:
        """Переключить способ формирования XML-патча."""
        mode = str(mode or "xml").lower()
        if mode not in ("dxml_lua", "xml", "xml_override"):
            raise ProjectError("Неизвестный режим XML-патча: %s" % mode)

        def command(project):
            previous = project.xml_patch_mode
            project.xml_patch_mode = mode
            try:
                self.repository.write_meta(project)
            except Exception:
                project.xml_patch_mode = previous
                raise
            return project

        project = self.session.write(command)
        self.invalidate_preview_cache()
        return project

    def module_setting(self, key, default=None):
        """Текущее значение настройки модуля у активного проекта."""
        state = self.session.state()
        if state is None:
            return default
        return state.module_settings.get(str(key or "").strip(), default)

    def module_settings(self) -> Dict[str, object]:
        """Копия всех сохранённых настроек модулей активного проекта."""
        state = self.session.state()
        if state is None:
            return {}
        return dict(state.module_settings)

    def set_module_setting(self, key, value):
        """Сохранить значение настройки модуля в активном проекте."""
        key = str(key or "").strip()
        if not key:
            raise ProjectError("Ключ настройки проекта не может быть пустым")

        def command(project):
            previous = project.module_settings.get(key, _MISSING_SETTING)
            project.module_settings[key] = value
            try:
                self.repository.write_meta(project)
            except Exception:
                if previous is _MISSING_SETTING:
                    project.module_settings.pop(key, None)
                else:
                    project.module_settings[key] = previous
                raise
            return value

        result = self.session.write(command)
        self.invalidate_preview_cache()
        return result

    # -------------------------------------------------- сессия вкладок
    def _tabs_path(self, project=None) -> Path:
        project = self._require_active() if project is None else project
        return Path(project.root_dir) / self.TABS_NAME

    @staticmethod
    def _normalize_tab_record(record):
        """Привести запись вкладки к безопасному виду для JSON."""
        if not isinstance(record, dict):
            return None

        tab_id = str(record.get("tab_id") or "").strip()
        if not tab_id:
            return None

        kind = str(record.get("kind") or "module").strip().lower()
        if kind not in ("editor", "resolved", "module"):
            kind = "module"

        source_path = record.get("source_path")
        source_path = str(source_path) if source_path else None

        state = {}
        raw_state = record.get("state")
        if isinstance(raw_state, dict):
            for key, value in raw_state.items():
                if value is None or isinstance(
                    value,
                    (str, bool, int, float),
                ):
                    state[str(key)[:64]] = value

        return {
            "tab_id": tab_id[:512],
            "title": str(record.get("title") or tab_id)[:512],
            "kind": kind,
            "owner": str(record.get("owner") or "")[:128],
            "source_path": source_path,
            "state": state,
            "active": bool(record.get("active")),
        }

    def save_tabs(self, tabs) -> bool:
        """Сохранить список открытых вкладок активного проекта."""
        project = self._require_active()
        records = []
        for record in tabs or ():
            normalized = self._normalize_tab_record(record)
            if normalized is not None:
                records.append(normalized)

        payload = {
            "version": self.TABS_VERSION,
            "tabs": records,
        }
        with self._write_lock:
            write_json_atomic(self._tabs_path(project), payload)
        return True

    def load_tabs(self) -> List[dict]:
        """Прочитать сохранённые вкладки активного проекта."""
        with self._state_lock:
            if self.active is None:
                return []
            path = self._tabs_path(self.active)

        if not path.is_file():
            return []

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning(
                "Проект: не удалось прочитать вкладки %s: %s",
                path,
                exc,
            )
            return []

        if isinstance(data, dict):
            values = data.get("tabs") or ()
        elif isinstance(data, list):
            values = data
        else:
            values = ()

        result = []
        for record in values:
            normalized = self._normalize_tab_record(record)
            if normalized is not None:
                result.append(normalized)
        return result

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

        self.invalidate_preview_cache()
        self._clear_overlay_cache()

        if not target.exists():
            self.session.deactivate()
            self._resolved_draft_files.clear()
            return True

        try:
            shutil.rmtree(str(target))
        except OSError as exc:
            raise ProjectError("Не удалось удалить проект: %s" % exc)

        self.session.deactivate()
        self._resolved_draft_files.clear()
        return True

    # -------------------------------------------------- черновики
    def update_draft(
        self,
        path,
        text: str,
        original_text=None,
    ) -> ProjectDraft:
        # Правка меняет только словари проекта; файловый ввод-вывод
        # сериализуется отдельным замком, чтобы не блокировать UI на
        # время записи многомегабайтного черновика.
        with self._state_lock:
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
        draft = self.update_draft(
            path,
            text if text is not None else self.get_text(path) or "",
        )
        saved_at = time.time()
        target = self.draft_store.text_target(project.root_dir, draft.path)
        payload = self.draft_store.build_text_payload(
            draft,
            saved_text=draft.current_text,
            saved_at=saved_at,
        )
        self.draft_store.write_payload(target, payload)
        with self._state_lock:
            draft.saved_text = draft.current_text
            draft.saved_at = saved_at
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
        with self._state_lock:
            project = self._require_active()
            draft = self._update_draft_locked(
                path,
                text,
                original_text=original_text,
            )
            saved_at = time.time()
            payload = self.draft_store.build_text_payload(
                draft,
                saved_text=draft.current_text,
                saved_at=saved_at,
            )
            target = self.draft_store.text_target(
                project.root_dir,
                draft.path,
            )
            return target, payload, draft.revision, saved_at

    def write_draft_payload(self, target, payload) -> None:
        """Записать заранее подготовленный JSON черновика (потокобезопасно)."""
        self.draft_store.write_payload(target, payload)

    def write_staged_draft(self, path, target, payload, revision) -> bool:
        """Записать снимок, если черновик не изменился с момента подготовки.

        Проверка и запись выполняются под одним замком: автосохранение
        или новая правка не могут вклиниться и записать устаревший текст
        поверх свежего.
        """
        with self._write_lock:
            with self._state_lock:
                draft = self.get_draft(path)
                if (
                    draft is not None
                    and int(draft.revision) != int(revision)
                ):
                    return False
                self.draft_store.write_payload(target, payload)
                return True

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

        # Снимок списков — под state-lock. Сама запись идёт под
        # write-lock, чтобы не держать блокировку состояния на время
        # сериализации десятков мегабайт (иначе UI встанет).
        with self._state_lock:
            drafts = [
                draft for draft in project.drafts.values()
                if draft.is_dirty
            ]
            assets = [
                draft for draft in project.assets.values()
                if draft.is_dirty
            ]
            resolved = [
                draft for draft in project.resolved_drafts.values()
                if draft.is_dirty
            ]

        for draft in drafts:
            text = str(draft.current_text or "")
            saved_at = time.time()
            target = self.draft_store.text_target(
                project.root_dir,
                draft.path,
            )
            payload = self.draft_store.build_text_payload(
                draft,
                saved_text=text,
                saved_at=saved_at,
            )
            self.draft_store.write_payload(target, payload)
            with self._state_lock:
                if draft.current_text == text:
                    draft.saved_text = text
                    draft.saved_at = saved_at
            saved.append(
                SavedDraft(
                    kind="file",
                    key=self.path_key(draft.path),
                    path=draft.path,
                    revision=int(draft.revision),
                    size=len(text),
                )
            )

        for draft in assets:
            data = bytes(draft.current_bytes)
            saved_at = time.time()
            self._write_asset_draft(
                project,
                draft,
                saved_bytes=data,
                saved_at=saved_at,
            )
            with self._state_lock:
                if draft.current_bytes == data:
                    draft.saved_bytes = data
                    draft.saved_at = saved_at
            saved.append(
                SavedDraft(
                    kind="asset",
                    key=self.path_key(draft.path),
                    path=draft.path,
                    revision=int(draft.revision),
                    size=len(data),
                )
            )

        for draft in resolved:
            text = str(draft.current_text or "")
            saved_at = time.time()
            self._write_resolved_draft(
                project,
                draft,
                saved_text=text,
                saved_at=saved_at,
            )
            with self._state_lock:
                if draft.current_text == text:
                    draft.saved_text = text
                    draft.saved_at = saved_at
            saved.append(
                SavedDraft(
                    kind="resolved",
                    key=self.resolved_key(draft.tab_id, draft.root_path),
                    path=draft.root_path,
                    revision=int(draft.revision),
                    size=len(text),
                )
            )

        return saved

    def flush(self) -> List[SavedDraft]:
        if self.active is None:
            return []
        return self.autosave()

    def get_text(self, path) -> Optional[str]:
        with self._state_lock:
            if self.active is None:
                return None
            draft = self.active.draft(path)
            return draft.current_text if draft is not None else None

    def get_draft(self, path) -> Optional[ProjectDraft]:
        with self._state_lock:
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
        self.draft_store.delete_text(project.root_dir, path)
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
        with self._state_lock:
            if self.active is None:
                return []
            return [
                draft
                for draft in self.active.drafts.values()
                if draft.has_changes
            ]

    def dirty_drafts(self) -> List[ProjectDraft]:
        with self._state_lock:
            if self.active is None:
                return []
            return [
                draft
                for draft in self.active.drafts.values()
                if draft.is_dirty
            ]

    # -------------------------------------------------- двоичные черновики
    def update_asset_draft(
        self,
        path,
        data: bytes,
        is_new: Optional[bool] = None,
    ) -> ProjectAssetDraft:
        """Обновить двоичный черновик в памяти (без записи в проект)."""
        with self._state_lock:
            return self._update_asset_draft_locked(path, data, is_new)

    def _update_asset_draft_locked(self, path, data, is_new=None):
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
        with self._state_lock:
            draft = project.assets.get(self.path_key(path))
        if draft is None:
            if data is None:
                raise ProjectError("Двоичный черновик не найден: %s" % path)
            draft = self.update_asset_draft(path, data)
        elif data is not None:
            draft = self.update_asset_draft(path, data)

        saved = bytes(draft.current_bytes)
        saved_at = time.time()
        self._write_asset_draft(
            project,
            draft,
            saved_bytes=saved,
            saved_at=saved_at,
        )
        with self._state_lock:
            if draft.current_bytes == saved:
                draft.saved_bytes = saved
                draft.saved_at = saved_at
        return draft

    def get_asset_draft(self, path) -> Optional[ProjectAssetDraft]:
        with self._state_lock:
            if self.active is None:
                return None
            return self.active.assets.get(self.path_key(path))

    def get_asset_bytes(self, path) -> Optional[bytes]:
        draft = self.get_asset_draft(path)
        return draft.current_bytes if draft is not None else None

    def remove_asset_draft(self, path) -> bool:
        project = self._require_active()
        key = self.path_key(path)
        with self._state_lock:
            draft = project.assets.pop(key, None)
        if draft is None:
            return False

        self._invalidate_preview_cache(path)
        self.draft_store.delete_asset(project.root_dir, path)
        return True

    def changed_asset_drafts(self) -> List[ProjectAssetDraft]:
        with self._state_lock:
            if self.active is None:
                return []
            return [
                draft
                for draft in self.active.assets.values()
                if draft.has_changes
            ]

    def dirty_asset_drafts(self) -> List[ProjectAssetDraft]:
        with self._state_lock:
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
        original_text = str(original_text or "")
        raw_current = str(current_text or "")
        current_text = collapse_duplicated_text(
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

        with self._state_lock:
            draft = project.resolved_drafts.get(key)
            if draft is None:
                draft = ProjectResolvedDraft(
                    root_path=Path(root_path),
                    title=str(title or ""),
                    original_text=original_text,
                    current_text=current_text,
                    saved_text=original_text,
                    tab_id=str(tab_id or ""),
                    line_sources=tuple(line_sources or ()),
                    revision=1,
                    updated_at=time.time(),
                )
                project.resolved_drafts[key] = draft
            else:
                draft.current_text = current_text
                draft.title = str(title or draft.title)
                draft.revision += 1
                draft.updated_at = time.time()
                if line_sources is not None:
                    draft.line_sources = tuple(line_sources)

        self._invalidate_preview_cache(Path(root_path))
        return draft

    def save_resolved_draft(
        self,
        tab_id,
        text: Optional[str] = None,
    ) -> ProjectResolvedDraft:
        project = self._require_active()
        key = self.resolved_key(tab_id, "")
        with self._state_lock:
            draft = project.resolved_drafts.get(key)
            if draft is None:
                raise ProjectError(
                    "Resolved-черновик не найден: %s" % tab_id
                )
            if text is not None:
                draft.current_text = collapse_duplicated_text(
                    str(text),
                    draft.original_text,
                )
                draft.revision += 1
            saved_text = draft.current_text
        saved_at = time.time()
        self._write_resolved_draft(
            project,
            draft,
            saved_text=saved_text,
            saved_at=saved_at,
        )
        with self._state_lock:
            if draft.current_text == saved_text:
                draft.saved_text = saved_text
                draft.saved_at = saved_at
        return draft

    def get_resolved_text(self, tab_id) -> Optional[str]:
        draft = self.get_resolved_draft(tab_id)
        return draft.current_text if draft is not None else None

    def get_resolved_draft(self, tab_id) -> Optional[ProjectResolvedDraft]:
        with self._state_lock:
            if self.active is None:
                return None
            key = str(tab_id)
            draft = self.active.resolved_drafts.get(key)
            if draft is None:
                draft = self.active.resolved_drafts.get(
                    self.resolved_key(tab_id, "")
                )
            return draft

    def remove_resolved_draft(self, tab_id) -> bool:
        project = self._require_active()
        key = str(tab_id)
        with self._state_lock:
            draft = project.resolved_drafts.pop(key, None)
        if draft is None:
            return False

        target = self._resolved_draft_files.pop(key, None)
        if target is None:
            target = self.draft_store.resolved_target(
                project.root_dir,
                draft,
            )
        self.draft_store.delete_resolved(target)
        return True

    def changed_resolved_drafts(self) -> List[ProjectResolvedDraft]:
        with self._state_lock:
            if self.active is None:
                return []
            return [
                draft
                for draft in self.active.resolved_drafts.values()
                if draft.has_changes
            ]

    def dirty_resolved_drafts(self) -> List[ProjectResolvedDraft]:
        with self._state_lock:
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
        state = self.session.state()
        if state is None:
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
        mode = normalize_save_mode(state.save_mode or "root")
        return (
            "Проект: %s | режим: %s | XML: %s | изменённых файлов: %d | "
            "несохранённых: %d"
            % (
                state.name,
                mode,
                state.xml_patch_mode,
                changed,
                dirty,
            )
        )

    # -------------------------------------------------- кэш предпросмотра
    def _require_active(self) -> Project:
        return self.session.require_active()

    def _write_draft(self, project: Project, draft: ProjectDraft) -> None:
        target = self.draft_store.text_target(project.root_dir, draft.path)
        payload = self.draft_store.build_text_payload(
            draft,
            saved_text=draft.saved_text,
            saved_at=draft.saved_at,
        )
        self.draft_store.write_payload(target, payload)

    def _write_asset_draft(
        self,
        project: Project,
        draft: ProjectAssetDraft,
        saved_bytes=None,
        saved_at=None,
    ) -> None:
        target = self.draft_store.asset_target(
            project.root_dir,
            draft.path,
        )
        payload = self.draft_store.build_asset_payload(
            draft,
            saved_bytes=saved_bytes,
            saved_at=saved_at,
        )
        self.draft_store.write_payload(target, payload)

    def _write_resolved_draft(
        self,
        project: Project,
        draft: ProjectResolvedDraft,
        saved_text=None,
        saved_at=None,
    ) -> None:
        with self._state_lock:
            # Не даём записать уже испорченный «двойной» текст.
            compact = collapse_duplicated_text(
                draft.current_text,
                draft.original_text,
            )
            if compact != draft.current_text:
                draft.current_text = compact
            saved = compact if saved_text is None else str(saved_text)

            key = self.resolved_key(draft.tab_id, draft.root_path)
            target = self.draft_store.resolved_target(
                project.root_dir,
                draft,
                existing=self._resolved_draft_files.get(key),
            )
            payload = self.draft_store.build_resolved_payload(
                draft,
                saved_text=saved,
                saved_at=saved_at,
            )

        self.draft_store.write_payload(target, payload)
        with self._state_lock:
            self._resolved_draft_files[key] = target

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
