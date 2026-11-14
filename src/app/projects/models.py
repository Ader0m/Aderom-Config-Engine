# -*- coding: utf-8 -*-
"""Модели проекта: без Qt и без зависимости от ProjectService."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core import GeneratedPatch, PatchPlan
from sdk.paths import path_key


def resolved_key(tab_id, root_path) -> str:
    """Ключ resolved-черновика: tab_id или путь корня."""
    value = str(tab_id or "").strip()
    if value:
        return value
    return "resolved:" + path_key(root_path)


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
    revision: int = 0
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
        return self.drafts.get(path_key(path))

    def resolved_draft(self, tab_id) -> Optional[ProjectResolvedDraft]:
        return self.resolved_drafts.get(str(tab_id))

    def asset_draft(self, path) -> Optional[ProjectAssetDraft]:
        return self.assets.get(path_key(path))

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


