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
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from core.fs import read_text_safe, read_xml_text
from core.patch_merge import merge_patch_plans
from core.patch_models import GeneratedPatch, PatchOptions, PatchPlan
from core.multiline_edit import analyze_multiline_edit


class ProjectError(Exception):
    """Ошибка работы с проектом."""


# ---------------------------------------------------------------- модели


@dataclass
class ProjectDraft:
    """Новая версия одного файла внутри проекта."""

    path: Path
    original_text: str
    current_text: str
    saved_text: str
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
    save_mode: str = "one_by_one"
    created_at: str = ""
    drafts: Dict[str, ProjectDraft] = field(default_factory=dict)
    resolved_drafts: Dict[str, ProjectResolvedDraft] = field(
        default_factory=dict
    )

    def draft(self, path) -> Optional[ProjectDraft]:
        return self.drafts.get(ProjectService.path_key(path))

    def resolved_draft(self, tab_id) -> Optional[ProjectResolvedDraft]:
        return self.resolved_drafts.get(str(tab_id))


@dataclass(frozen=True)
class ProjectInfo:
    """Краткая информация о проекте для списка загрузки."""

    id: str
    name: str
    root_dir: Path
    created_at: str
    draft_count: int
    save_mode: str = "one_by_one"


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

    @property
    def ok(self) -> bool:
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
        return tuple(item for item in self.items if item.ok)

    @property
    def has_errors(self) -> bool:
        return any(not item.ok for item in self.items)

    @property
    def patch_count(self) -> int:
        return len(self.ok_items)


# ---------------------------------------------------------------- сервис


class ProjectService:
    """Хранилище проектов и черновиков."""

    META_NAME = "project.json"
    DRAFTS_DIR = "drafts"
    RESOLVED_DIR = "resolved"

    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.active: Optional[Project] = None

    # -------------------------------------------------- свойства
    @property
    def is_active(self) -> bool:
        return self.active is not None

    def patch_mode(self) -> str:
        if self.active is None:
            return "one_by_one"
        mode = str(self.active.save_mode or "one_by_one")
        if mode not in ("one_by_one", "all_in_one"):
            return "one_by_one"
        return mode

    @staticmethod
    def path_key(path) -> str:
        return os.path.normcase(os.path.abspath(str(path)))

    def _draft_filename(self, path) -> str:
        digest = hashlib.sha1(
            self.path_key(path).encode("utf-8", "replace")
        ).hexdigest()
        return digest + ".json"

    def _project_dir(self, project_id: str) -> Path:
        return self.base_dir / project_id

    # -------------------------------------------------- создание / загрузка
    def create(
        self,
        name: str,
        modules: Optional[Iterable[str]] = None,
        save_mode: str = "one_by_one",
    ) -> Project:
        name = (name or "").strip()
        if not name:
            raise ProjectError("Название проекта не может быть пустым")

        if self.active is not None:
            self.close()

        project_id = self._make_project_id(name)
        root_dir = self._project_dir(project_id)
        root_dir.mkdir(parents=True, exist_ok=False)
        (root_dir / self.DRAFTS_DIR).mkdir(parents=True, exist_ok=True)
        (root_dir / self.RESOLVED_DIR).mkdir(parents=True, exist_ok=True)

        project = Project(
            id=project_id,
            name=name,
            root_dir=root_dir,
            modules=list(modules or []),
            save_mode=save_mode or "one_by_one",
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

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ProjectError("Не удалось прочитать проект: %s" % exc)

        project = Project(
            id=str(meta.get("id") or project_id),
            name=str(meta.get("name") or project_id),
            root_dir=root_dir,
            modules=list(meta.get("modules") or []),
            save_mode=str(meta.get("save_mode") or "one_by_one"),
            created_at=str(meta.get("created_at") or ""),
            drafts={},
            resolved_drafts={},
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

        self.active = project
        return project

    def list_projects(self) -> List[ProjectInfo]:
        result: List[ProjectInfo] = []

        for entry in sorted(self.base_dir.iterdir()):
            if not entry.is_dir():
                continue
            meta_path = entry / self.META_NAME
            if not meta_path.is_file():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                continue

            drafts_dir = entry / self.DRAFTS_DIR
            resolved_dir = entry / self.RESOLVED_DIR
            draft_count = 0
            if drafts_dir.is_dir():
                draft_count += len(list(drafts_dir.glob("*.json")))
            if resolved_dir.is_dir():
                draft_count += len(list(resolved_dir.glob("*.json")))

            result.append(
                ProjectInfo(
                    id=str(meta.get("id") or entry.name),
                    name=str(meta.get("name") or entry.name),
                    root_dir=entry,
                    created_at=str(meta.get("created_at") or ""),
                    draft_count=draft_count,
                    save_mode=str(
                        meta.get("save_mode") or "one_by_one"
                    ),
                )
            )

        return result

    def close(self) -> None:
        if self.active is None:
            return
        self.flush()
        self.active = None

    def set_patch_mode(self, mode: str) -> Project:
        """Переключить режим формирования патча у активного проекта."""
        project = self._require_active()
        mode = str(mode or "one_by_one")
        if mode not in ("one_by_one", "all_in_one"):
            raise ProjectError("Неизвестный режим формирования патча: %s" % mode)

        project.save_mode = mode
        self._write_meta(project)
        return project

    def delete_active_project(self) -> bool:
        """Удалить активный проект со всеми черновиками.

        Это разрушительная операция: проект не сохраняется и не переносится
        в корзину. Каталог удаляется только если он находится внутри
        хранилища проектов.
        """
        project = self._require_active()
        root = Path(project.root_dir)

        base = Path(self.base_dir).resolve()
        target = root.resolve()

        try:
            common = os.path.commonpath([str(target), str(base)])
            inside = os.path.normcase(common) == os.path.normcase(str(base))
        except (ValueError, OSError):
            inside = False

        if not inside:
            raise ProjectError(
                "Отказ: каталог проекта находится вне хранилища проектов"
            )

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
                current_text=str(text or ""),
                saved_text=original,
                updated_at=time.time(),
                saved_at=0.0,
            )
            project.drafts[key] = draft
        else:
            draft.current_text = str(text or "")
            draft.updated_at = time.time()

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
        changed = len(self.changed_drafts()) + len(
            self.changed_resolved_drafts()
        )
        dirty = len(self.dirty_drafts()) + len(
            self.dirty_resolved_drafts()
        )
        mode = (
            "all in one"
            if self.patch_mode() == "all_in_one"
            else "one by one"
        )
        return (
            "Проект: %s | режим: %s | изменённых файлов: %d | "
            "несохранённых: %d"
            % (self.active.name, mode, changed, dirty)
        )

    # -------------------------------------------------- предпросмотр патча
    def build_preview(self, services, forest, log=None) -> ProjectPatchPreview:
        """Собрать предпросмотр в выбранном режиме проекта."""
        if self.patch_mode() == "all_in_one":
            return self._build_preview_all_in_one(
                services,
                forest,
                log=log,
            )
        return self._build_preview_one_by_one(
            services,
            forest,
            log=log,
        )

    def _build_preview_one_by_one(
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
                updated_at=draft.updated_at,
                saved_at=draft.saved_at,
            )
            for draft in sorted(
                self.changed_drafts(),
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

        def add_item(
            index,
            title,
            source_path,
            original_text,
            current_text,
            kind,
            draft=None,
            resolved=None,
        ):
            if log:
                log("Проект: подготовка %s" % title)

            if Path(source_path).suffix.lower() != ".ltx":
                items.append(
                    PreviewItem(
                        title=title,
                        source_path=Path(source_path),
                        plan=None,
                        generated=None,
                        error=(
                            "Патчи пока поддерживаются только для .ltx; "
                            "для XML нужен DXML-генератор"
                        ),
                        kind=kind,
                        draft=draft,
                        resolved=resolved,
                        notes=(),
                    )
                )
                return

            notes: Tuple[str, ...] = ()
            resolved_analysis = None
            if kind == "resolved" and resolved is not None:
                resolved_analysis = analyze_multiline_edit(
                    original_text,
                    current_text,
                    resolved.line_sources,
                )
                notes = resolved_analysis.notes

                if resolved_analysis.has_errors:
                    items.append(
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
                    items.append(
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
            )

            try:
                if kind == "resolved" and resolved is not None:
                    plan = services.patch.plan_multiline_edit(
                        source_path,
                        original_text,
                        current_text,
                        line_sources=resolved.line_sources,
                        forest=forest,
                        options=options,
                    )
                else:
                    plan = services.patch.plan_document_change(
                        source_path,
                        original_text,
                        current_text,
                        forest,
                        options,
                    )

                if plan.can_generate and generate:
                    generated = services.generate_patch(plan)
                else:
                    generated = None

                items.append(
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
            except Exception as exc:
                items.append(
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
            add_item(
                index,
                draft.path.name,
                draft.path,
                draft.original_text,
                draft.current_text,
                "file",
                draft=draft,
            )

        for draft in resolved_drafts:
            index += 1
            add_item(
                index,
                draft.title or ("%s (resolved)" % draft.root_path.name),
                draft.root_path,
                draft.original_text,
                draft.current_text,
                "resolved",
                resolved=draft,
            )

        return ProjectPatchPreview(project=project, items=tuple(items))

    def _build_preview_all_in_one(self, services, forest, log=None):
        """Один патч на корень загрузки."""
        base = self._build_preview_one_by_one(
            services,
            forest,
            log=log,
            generate=False,
        )

        items: List[PreviewItem] = []
        groups = {}

        for item in base.items:
            if (
                item.plan is None
                or not item.plan.can_generate
                or item.plan.target.root_path is None
            ):
                items.append(item)
                continue

            key = str(item.plan.target.root_path).lower()
            groups.setdefault(key, []).append(item)

        for key in sorted(groups):
            group = groups[key]
            root_path = group[0].plan.target.root_path
            title = "%s (all in one)" % Path(root_path).name

            notes = ["Режим: all in one", "Файлов: %d" % len(group)]
            for item in group:
                notes.extend(item.notes)

            try:
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
            "updated_at": draft.updated_at,
            "saved_at": draft.saved_at,
        }
        target = drafts_dir / self._draft_filename(draft.path)
        target.write_text(
            json.dumps(data, ensure_ascii=False),
            encoding="utf-8",
        )

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
                updated_at=float(data.get("updated_at") or 0.0),
                saved_at=float(data.get("saved_at") or 0.0),
            )
        except Exception:
            return None

    def _read_draft(self, draft_path: Path) -> Optional[ProjectDraft]:
        try:
            data = json.loads(draft_path.read_text(encoding="utf-8"))
            path = Path(data["path"])
            return ProjectDraft(
                path=path,
                original_text=str(data.get("original_text") or ""),
                current_text=str(data.get("current_text") or ""),
                saved_text=str(data.get("saved_text") or ""),
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
