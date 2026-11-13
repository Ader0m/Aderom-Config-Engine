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
import os
import re
import shutil
import time
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from core.tools.fs import read_text_safe, read_xml_text
from core.models import LineSource
from core.patching.ltx.merge import merge_patch_plans
from core.patching.models import (
    GeneratedPatch,
    PatchFile,
    PatchIssue,
    PatchOptions,
    PatchPlan,
    PatchTarget,
    VerificationCheck,
    VerificationResult,
)
from core.patching.ltx.multiline import analyze_multiline_edit

from .project_assets import (
    ProjectAssetRegistry,
    ProjectAssetTypeError,
)
from .project_readers import FileReaderError, ProjectFileReaderRegistry


class ProjectError(Exception):
    """Ошибка работы с проектом."""


SAVE_MODES = ("root", "on_file", "override")
_LEGACY_SAVE_MODES = {"in_place": "on_file"}


def normalize_save_mode(mode) -> str:
    """Привести режим проекта к актуальному имени.

    ``in_place`` — старое имя режима, который патчил файл-победитель
    строки. Для DLTX это неверно, поэтому старые проекты переводятся в
    ``on_file``: отдельный патч на каждый изменённый файл, но всегда для
    корня загрузки.
    """
    value = str(mode or "root").strip().lower().replace("-", "_")
    value = _LEGACY_SAVE_MODES.get(value, value)
    if value not in SAVE_MODES:
        raise ProjectError(
            "Неизвестный режим формирования патча: %s" % mode
        )
    return value


# ---------------------------------------------------------------- модели


@dataclass
class ProjectDraft:
    """Новая версия одного файла внутри проекта."""

    path: Path
    original_text: str
    current_text: str
    saved_text: str
    is_new: bool = False
    revision: int = 0
    updated_at: float = 0.0
    saved_at: float = 0.0

    @property
    def is_dirty(self) -> bool:
        """Есть изменения, ещё не записанные в каталог проекта."""
        return self.current_text != self.saved_text

    @property
    def has_changes(self) -> bool:
        """Есть изменения относительно исходного файла."""
        return self.current_text != self.original_text


@dataclass
class ProjectAssetDraft:
    """Черновик двоичного ресурса (звук, текстура и т.п.).

    Проект хранит не текст, а готовые байты файла. Это нужно для
    форматов, у которых нет текстового представления (.ogg): правка
    комментария пересобирает контейнер, а патч-мод получает целый файл.
    """

    path: Path
    original_sha1: str
    current_bytes: bytes
    saved_bytes: bytes
    is_new: bool = False
    revision: int = 0
    updated_at: float = 0.0
    saved_at: float = 0.0

    @property
    def current_sha1(self) -> str:
        return hashlib.sha1(self.current_bytes).hexdigest()

    @property
    def is_dirty(self) -> bool:
        """Есть изменения, ещё не записанные в каталог проекта."""
        return self.current_bytes != self.saved_bytes

    @property
    def has_changes(self) -> bool:
        """Есть изменения относительно исходного файла."""
        return self.current_sha1 != self.original_sha1

    @property
    def size(self) -> int:
        return len(self.current_bytes)


@dataclass(frozen=True)
class AssetChange:
    """Изменение двоичного ресурса (для PatchPlan.changes)."""

    kind: str
    path: Path
    description: str
    risk: str = "safe"


@dataclass
class ProjectResolvedDraft:
    """Черновик resolved-файла: правка итогового текста."""

    root_path: Path
    title: str
    original_text: str
    current_text: str
    saved_text: str
    tab_id: str = ""
    line_sources: tuple = ()
    updated_at: float = 0.0
    saved_at: float = 0.0

    @property
    def is_dirty(self) -> bool:
        return self.current_text != self.saved_text

    @property
    def has_changes(self) -> bool:
        return self.current_text != self.original_text


@dataclass
class Project:
    """Активный проект."""

    id: str
    name: str
    root_dir: Path
    modules: List[str] = field(default_factory=list)
    save_mode: str = "root"
    xml_patch_mode: str = "xml"
    created_at: str = ""
    drafts: Dict[str, ProjectDraft] = field(default_factory=dict)
    resolved_drafts: Dict[str, ProjectResolvedDraft] = field(
        default_factory=dict
    )
    assets: Dict[str, ProjectAssetDraft] = field(default_factory=dict)
    module_settings: Dict[str, object] = field(default_factory=dict)

    def draft(self, path) -> Optional[ProjectDraft]:
        return self.drafts.get(ProjectService.path_key(path))

    def resolved_draft(self, tab_id) -> Optional[ProjectResolvedDraft]:
        return self.resolved_drafts.get(str(tab_id))

    def asset_draft(self, path) -> Optional[ProjectAssetDraft]:
        return self.assets.get(ProjectService.path_key(path))

    def module_setting(self, key, default=None):
        return self.module_settings.get(str(key or "").strip(), default)


@dataclass(frozen=True)
class ProjectInfo:
    """Краткая информация о проекте для списка загрузки."""

    id: str
    name: str
    root_dir: Path
    created_at: str
    draft_count: int
    save_mode: str = "root"
    xml_patch_mode: str = "xml"


@dataclass(frozen=True)
class SavedDraft:
    """Событие автосохранения черновика."""

    kind: str          # file | resolved
    key: str
    path: Path
    text: str


@dataclass(frozen=True)
class PreviewItem:
    """Один файл проекта в предпросмотре патча."""

    title: str
    source_path: Path
    plan: Optional[PatchPlan]
    generated: Optional[GeneratedPatch]
    error: str = ""
    kind: str = "file"
    draft: Optional[ProjectDraft] = None
    resolved: Optional[ProjectResolvedDraft] = None
    notes: Tuple[str, ...] = ()
    text: str = ""

    @property
    def is_patch(self) -> bool:
        return self.kind not in ("info", "script")

    @property
    def ok(self) -> bool:
        if not self.is_patch:
            return not self.error
        if self.error:
            return False
        if self.plan is None or self.generated is None:
            return False
        return bool(
            self.generated.verification
            and self.generated.verification.ok
        )


@dataclass(frozen=True)
class ProjectPatchPreview:
    """Результат подготовки патча по проекту."""

    project: Project
    items: Tuple[PreviewItem, ...] = ()

    @property
    def ok_items(self) -> Tuple[PreviewItem, ...]:
        return tuple(
            item for item in self.items
            if item.is_patch and item.ok
        )

    @property
    def has_errors(self) -> bool:
        return any(
            item.is_patch and not item.ok
            for item in self.items
        )

    @property
    def patch_count(self) -> int:
        return len(self.ok_items)


# ---------------------------------------------------------------- сервис


class ProjectService:
    """Хранилище проектов и черновиков."""

    META_NAME = "project.json"
    DRAFTS_DIR = "drafts"
    RESOLVED_DIR = "resolved"
    ASSETS_DIR = "assets"
    ROOTS_NAME = "project_roots.json"

    PREVIEW_CACHE_SIZE = 128

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
        return os.path.normcase(os.path.abspath(str(path)))

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
                if draft is not None:
                    project.resolved_drafts[
                        self.resolved_key(draft.tab_id, draft.root_path)
                    ] = draft

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
            return True

        try:
            shutil.rmtree(str(target))
        except OSError as exc:
            raise ProjectError("Не удалось удалить проект: %s" % exc)

        self.active = None
        return True

    # -------------------------------------------------- черновики
    def update_draft(self, path, text: str) -> ProjectDraft:
        project = self._require_active()
        path = Path(path)
        key = self.path_key(path)
        draft = project.drafts.get(key)

        new_text = str(text or "")

        if draft is None:
            if path.is_file():
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
        tab_id = str(tab_id or "").strip()
        if tab_id:
            return tab_id
        return "resolved:" + ProjectService.path_key(root_path)

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

        if draft is None:
            draft = ProjectResolvedDraft(
                root_path=Path(root_path),
                title=str(title or ""),
                original_text=str(original_text or ""),
                current_text=str(current_text or ""),
                saved_text=str(original_text or ""),
                tab_id=str(tab_id or ""),
                line_sources=tuple(line_sources or ()),
                updated_at=time.time(),
            )
            project.resolved_drafts[key] = draft
        else:
            draft.current_text = str(current_text or "")
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
            draft.current_text = str(text)

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
    @staticmethod
    def _digest(text) -> str:
        return hashlib.sha1(
            str(text or "").encode("utf-8", "replace")
        ).hexdigest()

    @staticmethod
    def _line_sources_signature(line_sources) -> str:
        digest = hashlib.sha1()
        for source in line_sources or ():
            digest.update(str(getattr(source, "kind", "")).encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(getattr(source, "path", "")).encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(getattr(source, "line", 0)).encode("ascii"))
            digest.update(b"\1")
        return digest.hexdigest()

    def _preview_project_key(self, forest):
        project = self._require_active()
        return (
            project.id,
            id(forest),
            str(getattr(forest, "scan_dir", "")),
            len(getattr(forest, "files", ()) or ()),
            len(getattr(forest, "xml_files", ()) or ()),
            project.save_mode,
            project.xml_patch_mode,
            project.name,
            tuple(
                sorted(
                    (str(key), repr(value))
                    for key, value in project.module_settings.items()
                )
            ),
        )

    def _draft_preview_key(self, draft, forest):
        return (
            "draft",
            self._preview_project_key(forest),
            self.path_key(draft.path),
            bool(draft.is_new),
            int(draft.revision),
            self._digest(draft.original_text),
            self._digest(draft.current_text),
        )

    def _asset_preview_key(self, draft, forest):
        return (
            "asset",
            self._preview_project_key(forest),
            self.path_key(draft.path),
            bool(draft.is_new),
            int(draft.revision),
            draft.original_sha1,
            draft.current_sha1,
        )

    def _resolved_preview_key(self, draft, forest):
        return (
            "resolved",
            self._preview_project_key(forest),
            str(draft.tab_id),
            self.path_key(draft.root_path),
            self._digest(draft.original_text),
            self._digest(draft.current_text),
            self._line_sources_signature(draft.line_sources),
        )

    def invalidate_preview_cache(self):
        """Сбросить кэш предпросмотра (например, после смены типов)."""
        self._preview_cache.clear()

    def _invalidate_preview_cache(self, path=None):
        if path is None:
            self._preview_cache.clear()
            return

        wanted = self.path_key(path)
        for key in list(self._preview_cache):
            if len(key) > 2 and key[2] == wanted:
                self._preview_cache.pop(key, None)

    def _cached_preview_items(self, key, factory):
        cached = self._preview_cache.get(key)
        if cached is not None:
            self._preview_cache.move_to_end(key)
            return list(cached)

        items = tuple(factory())
        self._preview_cache[key] = items
        self._preview_cache.move_to_end(key)
        while len(self._preview_cache) > self.PREVIEW_CACHE_SIZE:
            self._preview_cache.popitem(last=False)
        return list(items)

    # -------------------------------------------------- предпросмотр патча
    def build_preview(self, services, forest, log=None) -> ProjectPatchPreview:
        """Собрать предпросмотр в выбранном режиме проекта."""
        if self.is_root_mode():
            return self._build_preview_root(
                services,
                forest,
                log=log,
            )
        return self._build_preview_per_file(
            services,
            forest,
            log=log,
        )

    @staticmethod
    def _preview_draft_items(add_item, *args, **kwargs):
        output = []
        add_item(*args, output=output, **kwargs)
        return tuple(output)

    @staticmethod
    def _patch_relative_path(forest, path):
        scan_dir = Path(getattr(forest, "scan_dir", "") or "")
        if not str(scan_dir):
            raise ProjectError("Не определён каталог gamedata/configs")

        relative = Path(
            os.path.relpath(str(path), str(scan_dir))
        )
        if relative.is_absolute() or ".." in relative.parts:
            raise ProjectError(
                "Новый файл находится вне gamedata/configs: %s" % path
            )
        return relative

    def _asset_relative_path(self, forest, path):
        """Путь ресурса от gamedata и его зарегистрированный тип."""
        scan_dir = Path(getattr(forest, "scan_dir", "") or "")
        if not str(scan_dir):
            raise ProjectError("Не определён каталог gamedata/configs")

        game_root = (
            scan_dir.parent
            if scan_dir.name.lower() == "configs"
            else scan_dir
        )

        try:
            return self.asset_registry.relative_for_path(game_root, path)
        except ProjectAssetTypeError as exc:
            raise ProjectError(str(exc))

    def _asset_preview_item(self, project, forest, draft, title):
        try:
            relative, asset_type = self._asset_relative_path(
                forest,
                draft.path,
            )
        except ProjectError as exc:
            return PreviewItem(
                title=title,
                source_path=Path(draft.path),
                plan=None,
                generated=None,
                error=str(exc),
                kind="asset",
                draft=None,
            )

        patch_rel = Path("gamedata") / relative
        plan = PatchPlan(
            source_path=Path(draft.path),
            target=PatchTarget(
                source_path=Path(draft.path),
                root_path=Path(getattr(forest, "scan_dir", "")).parent,
                reason="Двоичный ресурс проекта",
            ),
            patch_mod="ACE Patch - %s" % project.name,
            patch_file_rel=relative,
            changes=(
                AssetChange(
                    kind="asset",
                    path=Path(draft.path),
                    description="%s: заменить файл целиком" % asset_type.label,
                ),
            ),
            issues=(),
            warnings=(),
            preview="",
            format="asset",
            patch_target_dir=asset_type.vfs_root,
        )
        view_text = ""
        reader_notes = []
        try:
            view = self.file_readers.read(
                draft.path,
                draft.current_bytes,
            )
        except FileReaderError as exc:
            reader_notes.append("Чтение: %s" % exc)
        else:
            if view is not None:
                view_text = str(view.text or "")
                if view.label:
                    reader_notes.append("Представление: %s" % view.label)
                reader_notes.extend(view.details)

        generated = GeneratedPatch(
            plan=plan,
            files=(
                PatchFile(
                    relative_path=patch_rel,
                    text="",
                    data=bytes(draft.current_bytes),
                ),
            ),
            text="",
            verification=VerificationResult(
                ok=True,
                checks=(
                    VerificationCheck(
                        name="asset",
                        ok=True,
                        detail="Файл будет записан целиком: %s" % patch_rel,
                    ),
                ),
            ),
        )
        return PreviewItem(
            title=title,
            source_path=Path(draft.path),
            plan=plan,
            generated=generated,
            error="",
            kind="asset",
            draft=None,
            notes=(
                "Тип: %s" % asset_type.label,
                "Ресурс: %s" % patch_rel,
                "Размер: %d байт" % len(draft.current_bytes),
                "Новый файл" if draft.is_new else "Замена существующего файла",
            ) + tuple(reader_notes),
            text=view_text or (
                "Двоичный файл: %d байт" % len(draft.current_bytes)
            ),
        )

    def _new_file_preview(self, project, forest, draft, title):
        try:
            relative_path = self._patch_relative_path(
                forest,
                draft.path,
            )
        except ProjectError as exc:
            return PreviewItem(
                title=title,
                source_path=Path(draft.path),
                plan=None,
                generated=None,
                error=str(exc),
                kind="new_file",
                draft=draft,
            )

        suffix = Path(draft.path).suffix.lower()
        if suffix not in (".ltx", ".xml"):
            return PreviewItem(
                title=title,
                source_path=Path(draft.path),
                plan=None,
                generated=None,
                error="Новый файл должен быть .ltx или .xml",
                kind="new_file",
                draft=draft,
            )

        if not str(draft.current_text or "").strip():
            return PreviewItem(
                title=title,
                source_path=Path(draft.path),
                plan=None,
                generated=None,
                error="Содержимое нового файла пустое",
                kind="new_file",
                draft=draft,
            )

        plan = PatchPlan(
            source_path=Path(draft.path),
            target=PatchTarget(
                source_path=Path(draft.path),
                root_path=None,
                reason="Новый файл проекта",
            ),
            patch_mod="ACE Patch - %s" % project.name,
            patch_file_rel=relative_path,
            changes=(),
            issues=(),
            warnings=(),
            preview=str(draft.current_text),
            format="xml" if suffix == ".xml" else "ltx",
        )
        generated = GeneratedPatch(
            plan=plan,
            files=(
                PatchFile(
                    relative_path=(
                        Path("gamedata") / "configs" / relative_path
                    ),
                    text=str(draft.current_text),
                ),
            ),
            text=str(draft.current_text),
            verification=VerificationResult(
                ok=True,
                checks=(
                    VerificationCheck(
                        name="new_file",
                        ok=True,
                        detail="Файл будет записан как новый",
                    ),
                ),
            ),
        )
        return PreviewItem(
            title=title,
            source_path=Path(draft.path),
            plan=plan,
            generated=generated,
            kind="new_file",
            draft=draft,
            notes=(
                "Новый файл: %s"
                % (Path("gamedata") / "configs" / relative_path),
            ),
            text=str(draft.current_text),
        )

    def _build_preview_per_file(
        self,
        services,
        forest,
        log=None,
        generate=True,
    ) -> ProjectPatchPreview:
        project = self._require_active()

        # Снимок черновиков: пользователь может продолжать печатать,
        # пока фоновая задача готовит предпросмотр.
        raw_drafts = [
            ProjectDraft(
                path=draft.path,
                original_text=draft.original_text,
                current_text=draft.current_text,
                saved_text=draft.saved_text,
                is_new=draft.is_new,
                revision=draft.revision,
                updated_at=draft.updated_at,
                saved_at=draft.saved_at,
            )
            for draft in sorted(
                self.changed_drafts(),
                key=lambda item: str(item.path).lower(),
            )
        ]

        asset_drafts = [
            ProjectAssetDraft(
                path=draft.path,
                original_sha1=draft.original_sha1,
                current_bytes=draft.current_bytes,
                saved_bytes=draft.saved_bytes,
                is_new=draft.is_new,
                revision=draft.revision,
                updated_at=draft.updated_at,
                saved_at=draft.saved_at,
            )
            for draft in sorted(
                self.changed_asset_drafts(),
                key=lambda item: str(item.path).lower(),
            )
        ]

        resolved_drafts = [
            ProjectResolvedDraft(
                root_path=draft.root_path,
                title=draft.title,
                original_text=draft.original_text,
                current_text=draft.current_text,
                saved_text=draft.saved_text,
                tab_id=draft.tab_id,
                line_sources=draft.line_sources,
                updated_at=draft.updated_at,
                saved_at=draft.saved_at,
            )
            for draft in sorted(
                self.changed_resolved_drafts(),
                key=lambda item: str(item.root_path).lower(),
            )
        ]

        items: List[PreviewItem] = []
        script_seen = set()

        def add_item(
            index,
            title,
            source_path,
            original_text,
            current_text,
            kind,
            draft=None,
            resolved=None,
            output=None,
        ):
            output = items if output is None else output

            if log:
                log("Проект: подготовка %s" % title)

            if draft is not None and draft.is_new:
                output.append(
                    self._new_file_preview(
                        project,
                        forest,
                        draft,
                        title,
                    )
                )
                return

            suffix_low = Path(source_path).suffix.lower()
            if suffix_low not in (".ltx", ".xml"):
                output.append(
                    PreviewItem(
                        title=title,
                        source_path=Path(source_path),
                        plan=None,
                        generated=None,
                        error=(
                            "Патчи поддерживаются только для .ltx и .xml; "
                            "получено: %s" % (suffix_low or "без расширения")
                        ),
                        kind=kind,
                        draft=draft,
                        resolved=resolved,
                        notes=(),
                    )
                )
                return

            is_xml = suffix_low == ".xml"
            override_mode = self.patch_mode() == "override"
            notes: Tuple[str, ...] = ()
            resolved_analysis = None
            if (
                kind == "resolved"
                and resolved is not None
                and not is_xml
                and not override_mode
            ):
                resolved_analysis = analyze_multiline_edit(
                    original_text,
                    current_text,
                    resolved.line_sources,
                )
                notes = resolved_analysis.notes

                if resolved_analysis.has_errors:
                    output.append(
                        PreviewItem(
                            title=title,
                            source_path=Path(source_path),
                            plan=None,
                            generated=None,
                            error=(
                                "Правка не поддержана: %s"
                                % "; ".join(
                                    issue.message
                                    for issue in resolved_analysis.issues
                                    if issue.severity == "error"
                                )
                            ),
                            kind=kind,
                            draft=draft,
                            resolved=resolved,
                            notes=notes,
                        )
                    )
                    return

                if not resolved_analysis.changes:
                    output.append(
                        PreviewItem(
                            title=title,
                            source_path=Path(source_path),
                            plan=None,
                            generated=None,
                            error="В resolved-виде нет изменений конфигурации",
                            kind=kind,
                            draft=draft,
                            resolved=resolved,
                            notes=notes,
                        )
                    )
                    return

            if index == 1:
                suffix = "ace"
            else:
                suffix = "ace_%s" % hashlib.sha1(
                    ("%s|%s" % (kind, source_path)).encode(
                        "utf-8",
                        "replace",
                    )
                ).hexdigest()[:6]

            options = PatchOptions(
                patch_mod="ACE Patch - %s" % project.name,
                suffix=suffix,
                project_tag=project.name,
                allow_delete_section=True,
                allow_delete_xml_element=True,
                xml_patch_mode=project.xml_patch_mode,
            )

            try:
                pre_generated = None
                fork_result = None
                fork_notes: Tuple[str, ...] = ()
                if (
                    not is_xml
                    and override_mode
                    and hasattr(services, "plan_ltx_override")
                ):
                    result = services.plan_ltx_override(
                        source_path,
                        original_text,
                        current_text,
                        line_sources=(
                            resolved.line_sources
                            if kind == "resolved" and resolved is not None
                            else ()
                        ),
                        forest=forest,
                        options=options,
                        resolved=(
                            kind == "resolved" and resolved is not None
                        ),
                    )
                    plan = result.plan
                    pre_generated = result.generated
                    notes = notes + tuple(
                        issue.message for issue in plan.warnings
                    )
                elif is_xml:
                    xml_setting = project.xml_patch_mode.lower()
                    if xml_setting == "xml_override":
                        # Override XML: полная перезапись файла тем, что
                        # получилось в редакторе.
                        options = replace(
                            options,
                            xml_patch_mode="full_copy",
                        )
                        xml_setting = "full_copy"

                    fork_mode = xml_setting == "xml"
                    if fork_mode and hasattr(services, "plan_xml_fork"):
                        result = services.plan_xml_fork(
                            source_path,
                            original_text,
                            current_text,
                            forest=forest,
                            options=options,
                            project_mode=self.patch_mode(),
                        )
                        fork_result = result
                        plan = result.plan
                        pre_generated = result.generated
                        fork_notes = tuple(
                            "Ключ: %s -> %s" % (key.old_key, key.new_key)
                            for key in result.keys
                        ) + tuple(
                            "Ссылка: %s" % (
                                getattr(ref.path, "name", ref.path)
                            )
                            for ref in result.references[:20]
                        )
                        notes = notes + fork_notes
                    elif kind == "resolved" and resolved is not None:
                        plan = services.patch.plan_xml_resolved_edit(
                            source_path,
                            original_text,
                            current_text,
                            line_sources=resolved.line_sources,
                            forest=forest,
                            options=options,
                        )
                    else:
                        plan = services.patch.plan_xml_document_change(
                            source_path,
                            original_text,
                            current_text,
                            forest=forest,
                            options=options,
                        )
                elif kind == "resolved" and resolved is not None:
                    plan = services.patch.plan_multiline_edit(
                        source_path,
                        original_text,
                        current_text,
                        line_sources=resolved.line_sources,
                        forest=forest,
                        options=options,
                        analysis=resolved_analysis,
                    )
                else:
                    plan = services.patch.plan_document_change(
                        source_path,
                        original_text,
                        current_text,
                        forest,
                        options,
                    )

                if (
                    not is_xml
                    and self.patch_mode() == "on_file"
                    and plan.target.root_path is not None
                ):
                    notes = notes + (
                        "On-file: отдельный патч на файл; корень — %s"
                        % Path(plan.target.root_path).name,
                    )

                if pre_generated is not None:
                    generated = pre_generated
                elif plan.can_generate and generate:
                    generated = services.generate_patch(plan)
                else:
                    generated = None

                output.append(
                    PreviewItem(
                        title=title,
                        source_path=Path(source_path),
                        plan=plan,
                        generated=generated,
                        error="" if plan.can_generate else "План содержит ошибки",
                        kind=kind,
                        draft=draft,
                        resolved=resolved,
                        notes=notes,
                    )
                )

                if fork_result is not None:
                    for ref in fork_result.references:
                        if ref.kind != "script":
                            continue
                        key = (
                            str(getattr(ref.path, "resolve", lambda: ref.path)()),
                            ref.line,
                        )
                        if key in script_seen:
                            continue
                        script_seen.add(key)
                        output.append(
                            PreviewItem(
                                title="Скрипт: %s:%d"
                                % (Path(ref.path).name, ref.line),
                                source_path=Path(ref.path),
                                plan=None,
                                generated=None,
                                error="",
                                kind="script",
                                notes=(ref.excerpt,),
                                text=ref.excerpt,
                            )
                        )
            except Exception as exc:
                output.append(
                    PreviewItem(
                        title=title,
                        source_path=Path(source_path),
                        plan=None,
                        generated=None,
                        error=str(exc),
                        kind=kind,
                        draft=draft,
                        resolved=resolved,
                        notes=notes,
                    )
                )

        index = 0
        for draft in raw_drafts:
            index += 1
            key = self._draft_preview_key(draft, forest)
            items.extend(
                self._cached_preview_items(
                    key,
                    lambda draft=draft, index=index: self._preview_draft_items(
                        add_item,
                        index,
                        draft.path.name,
                        draft.path,
                        draft.original_text,
                        draft.current_text,
                        "file",
                        draft=draft,
                    ),
                )
            )

        for draft in asset_drafts:
            index += 1
            key = self._asset_preview_key(draft, forest)
            items.extend(
                self._cached_preview_items(
                    key,
                    lambda draft=draft: (
                        self._asset_preview_item(
                            project,
                            forest,
                            draft,
                            draft.path.name,
                        ),
                    ),
                )
            )

        for draft in resolved_drafts:
            index += 1
            key = self._resolved_preview_key(draft, forest)
            title = draft.title or (
                "%s (resolved)" % draft.root_path.name
            )
            items.extend(
                self._cached_preview_items(
                    key,
                    lambda draft=draft, index=index, title=title: (
                        self._preview_draft_items(
                            add_item,
                            index,
                            title,
                            draft.root_path,
                            draft.original_text,
                            draft.current_text,
                            "resolved",
                            resolved=draft,
                        )
                    ),
                )
            )

        deduped = []
        seen_scripts = set()
        for item in items:
            if item.kind == "script":
                key = (self.path_key(item.source_path), item.text)
                if key in seen_scripts:
                    continue
                seen_scripts.add(key)
            deduped.append(item)

        return ProjectPatchPreview(project=project, items=tuple(deduped))

    def _build_preview_root(self, services, forest, log=None):
        """Один патч на корень загрузки; XML — один пакет на проект."""
        base = self._build_preview_per_file(
            services,
            forest,
            log=log,
            generate=False,
        )

        items: List[PreviewItem] = []
        groups = {}

        for item in base.items:
            if (
                item.kind == "asset"
                or item.kind == "new_file"
                or item.plan is None
                or not item.plan.can_generate
                or (
                    item.plan.target.root_path is None
                    and not item.plan.is_xml
                )
            ):
                items.append(item)
                continue

            if item.plan.is_xml:
                key = "xml:project"
            else:
                key = str(item.plan.target.root_path).lower()
            groups.setdefault(key, []).append(item)

        for key in sorted(groups):
            group = groups[key]
            is_xml = group[0].plan.is_xml
            root_path = (
                group[0].source_path
                if is_xml
                else group[0].plan.target.root_path
            )
            title = (
                "XML проекта (Root)"
                if is_xml
                else "%s (Root)" % Path(root_path).name
            )

            notes = ["Режим: Root", "Файлов: %d" % len(group)]
            for item in group:
                notes.extend(item.notes)

            try:
                if is_xml:
                    merged, generated = self._merge_xml_generated(
                        services,
                        group,
                    )
                else:
                    merged = merge_patch_plans(
                        [item.plan for item in group],
                        patch_mod=group[0].plan.patch_mod,
                    )
                    generated = (
                        services.generate_patch(merged)
                        if merged.can_generate
                        else None
                    )
                items.append(
                    PreviewItem(
                        title=title,
                        source_path=Path(root_path),
                        plan=merged,
                        generated=generated,
                        error="" if merged.can_generate else "План содержит ошибки",
                        kind="root",
                        notes=tuple(notes),
                    )
                )
            except Exception as exc:
                items.append(
                    PreviewItem(
                        title=title,
                        source_path=Path(root_path),
                        plan=None,
                        generated=None,
                        error=str(exc),
                        kind="root",
                        notes=tuple(notes),
                    )
                )

        return ProjectPatchPreview(project=base.project, items=tuple(items))

    def _merge_xml_generated(self, services, group):
        """Собрать XML-патч проекта: копии файлов + один файл добавлений."""
        generated_items = []
        changes = []
        issues = []
        warnings = []
        checks = []
        all_ok = True
        modes = {}

        for item in group:
            plan = item.plan
            changes.extend(plan.changes)
            issues.extend(plan.issues)
            warnings.extend(plan.warnings)

            source_key = str(item.source_path).lower()
            mode = plan.xml_mode
            if source_key in modes and modes[source_key] != mode:
                issues.append(
                    PatchIssue(
                        code="xml_mode_conflict",
                        message=(
                            "Один XML-файл изменён и как сырой, и как "
                            "resolved: выберите один режим, иначе патчи "
                            "могут противоречить друг другу"
                        ),
                        severity="error",
                    )
                )
                all_ok = False
            modes[source_key] = mode

            if not plan.can_generate:
                all_ok = False
                continue

            if getattr(plan, "format", "") == "xml_fork" and item.generated is not None:
                generated = item.generated
            else:
                generated = services.generate_patch(plan)
            if generated.verification is None or not generated.verification.ok:
                all_ok = False
            if generated.verification is not None:
                checks.extend(generated.verification.checks)
                issues.extend(generated.verification.issues)
            generated_items.append((item, generated))

        first_plan = group[0].plan
        merged_plan = replace(
            first_plan,
            changes=tuple(changes),
            issues=tuple(issues),
            warnings=tuple(warnings),
            preview="",
            diff=None,
        )

        files = []
        for _item, generated in generated_items:
            files.extend(generated.files)

        preview_parts = []
        for file in files:
            preview_parts.append("-- Файл: %s" % file.relative_path)
            preview_parts.append(file.text.rstrip())
            preview_parts.append("")
        preview = "\n".join(preview_parts)

        verification = VerificationResult(
            ok=all_ok and bool(files),
            checks=tuple(checks),
            issues=tuple(issues),
        )
        generated = GeneratedPatch(
            plan=merged_plan,
            files=tuple(files),
            text=preview,
            verification=verification,
        )
        return merged_plan, generated

    # -------------------------------------------------- служебное
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

        data = {
            "tab_id": draft.tab_id,
            "root_path": str(draft.root_path),
            "title": draft.title,
            "original_text": draft.original_text,
            "current_text": draft.current_text,
            "saved_text": draft.saved_text,
            "line_sources": self._serialize_line_sources(
                draft.line_sources
            ),
            "updated_at": draft.updated_at,
            "saved_at": draft.saved_at,
        }
        target = resolved_dir / self._resolved_draft_filename(draft)
        target.write_text(
            json.dumps(data, ensure_ascii=False),
            encoding="utf-8",
        )

    def _read_resolved_draft(
        self,
        draft_path: Path,
    ) -> Optional[ProjectResolvedDraft]:
        try:
            data = json.loads(draft_path.read_text(encoding="utf-8"))
            return ProjectResolvedDraft(
                root_path=Path(data["root_path"]),
                title=str(data.get("title") or ""),
                original_text=str(data.get("original_text") or ""),
                current_text=str(data.get("current_text") or ""),
                saved_text=str(data.get("saved_text") or ""),
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
    def _serialize_line_sources(line_sources) -> list:
        result = []
        for source in line_sources or ():
            path = getattr(source, "path", None)
            result.append(
                {
                    "kind": str(getattr(source, "kind", "") or ""),
                    "display": str(getattr(source, "display", "") or ""),
                    "path": str(path) if path is not None else "",
                    "line": int(getattr(source, "line", 0) or 0),
                    "open_raw": bool(getattr(source, "open_raw", False)),
                }
            )
        return result

    @staticmethod
    def _deserialize_line_sources(values) -> tuple:
        result = []
        for data in values or ():
            if not isinstance(data, dict):
                continue
            path = str(data.get("path") or "")
            result.append(
                LineSource(
                    kind=str(data.get("kind") or "none"),
                    display=str(data.get("display") or ""),
                    path=Path(path) if path else None,
                    line=int(data.get("line") or 0),
                    open_raw=bool(data.get("open_raw")),
                )
            )
        return tuple(result)

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
