# -*- coding: utf-8 -*-
"""Построение предпросмотра патча проекта.

Сценарий прикладного слоя: получает снимок проекта (режимы, настройки и
копии изменённых черновиков), поднимает core-планы и возвращает
``ProjectPatchPreview``. Не знает про Qt, UI и активный проект: это
входные данные.

Планы и патчи собирают core-движки; ``app`` владеет только DTO
предпросмотра и оркестрацией.
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import List, Mapping, Tuple

from sdk.contracts import ProjectOverlay

from core.api.documents import analyze_multiline_edit
from core.api.patches import (
    AssetPatchEngine,
    NewFilePatchEngine,
    PatchIntent,
    PatchOptions,
    PatchOrchestrator,
    PatchPlanningError,
    PatchSnapshot,
    merge_patch_plans,
    merge_xml_generated,
)
from core.api.paths import path_key
from core.api.vfs import game_root_from_scan_dir

from app.projects.assets import ProjectAssetTypeError
from app.projects.models import (
    Project,
    ProjectDraft,
    ProjectAssetDraft,
    ProjectError,
    ProjectResolvedDraft,
    normalize_save_mode,
    resolved_key,
)
from app.patching.models import (
    AssetChange,
    PreviewItem,
    ProjectPatchPreview,
)
from app.projects.readers import FileReaderError


def _is_mod_ltx(path) -> bool:
    """True для DLTX-патча ``mod_*.ltx``.

    Такие файлы не являются корнями загрузки и не участвуют в построении
    DLTX-дельты: их правка всегда записывается полной заменой файла.
    """
    path = Path(path)
    return (
        path.suffix.lower() == ".ltx"
        and path.name.lower().startswith("mod_")
    )


@dataclass(frozen=True)
class PreviewSnapshot:
    """Вход предпросмотра: проект, режимы и копии изменённых черновиков.

    ``project`` — сам объект проекта: UI сравнивает идентичность, чтобы
    отбросить устаревший предпросмотр. Тексты черновиков скопированы,
    чтобы фоновая сборка не зависела от дальнейших правок.
    """

    project: Project
    project_id: str
    project_name: str
    save_mode: str
    xml_patch_mode: str
    module_settings: Mapping[str, object]
    drafts: Tuple[ProjectDraft, ...]
    resolved_drafts: Tuple[ProjectResolvedDraft, ...]
    assets: Tuple[ProjectAssetDraft, ...]

    @classmethod
    def capture(
        cls,
        project: Project,
        *,
        save_mode,
        xml_patch_mode,
        module_settings,
    ) -> "PreviewSnapshot":
        """Снять снимок. Вызывающий держит блокировку состояния проекта."""
        drafts = tuple(
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
                (
                    item for item in project.drafts.values()
                    if item.has_changes
                ),
                key=lambda item: str(item.path).lower(),
            )
        )
        assets = tuple(
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
                (
                    item for item in project.assets.values()
                    if item.has_changes
                ),
                key=lambda item: str(item.path).lower(),
            )
        )
        resolved_drafts = tuple(
            ProjectResolvedDraft(
                root_path=draft.root_path,
                title=draft.title,
                original_text=draft.original_text,
                current_text=draft.current_text,
                saved_text=draft.saved_text,
                tab_id=draft.tab_id,
                line_sources=draft.line_sources,
                revision=draft.revision,
                updated_at=draft.updated_at,
                saved_at=draft.saved_at,
            )
            for draft in sorted(
                (
                    item
                    for item in project.resolved_drafts.values()
                    if item.has_changes
                ),
                key=lambda item: str(item.root_path).lower(),
            )
        )
        return cls(
            project=project,
            project_id=str(project.id),
            project_name=str(project.name),
            save_mode=str(save_mode),
            xml_patch_mode=str(xml_patch_mode),
            module_settings=module_settings,
            drafts=drafts,
            resolved_drafts=resolved_drafts,
            assets=assets,
        )


class ProjectPreviewBuilder:
    """Сборка предпросмотра патча по снимку проекта."""

    PREVIEW_CACHE_SIZE = 128
    # Кэш предпросмотра ограничен и по объёму: GeneratedPatch хранит
    # полные тексты файлов, а не только план.
    PREVIEW_CACHE_MAX_CHARS = 32_000_000

    def __init__(self, asset_registry, file_readers):
        self.asset_registry = asset_registry
        self.file_readers = file_readers
        # Предпросмотр asset/new-file не требует CoreComposition: движки
        # собирают план и патч из уже разрешённых данных проекта.
        self._file_patch_orchestrator = PatchOrchestrator(
            engines=(
                AssetPatchEngine(),
                NewFilePatchEngine(),
            ),
        )
        self._preview_cache = OrderedDict()
        self._preview_cache_sizes = {}
        self._preview_cache_chars = 0
        self._preview_lock = threading.RLock()

    # -------------------------------------------------- кэш
    def invalidate(self, path=None):
        """Сбросить кэш предпросмотра целиком или для одного пути."""
        with self._preview_lock:
            if path is None:
                self._preview_cache.clear()
                self._preview_cache_sizes.clear()
                self._preview_cache_chars = 0
                return

            wanted = path_key(path)
            for key in list(self._preview_cache):
                matches = (
                    len(key) > 2
                    and key[0] in ("draft", "asset")
                    and key[2] == wanted
                ) or (
                    len(key) > 3
                    and key[0] == "resolved"
                    and key[3] == wanted
                )
                if matches:
                    self._preview_cache.pop(key, None)
                    self._preview_cache_chars -= self._preview_cache_sizes.pop(
                        key,
                        0,
                    )
            if self._preview_cache_chars < 0:
                self._preview_cache_chars = 0

    # -------------------------------------------------- ключи
    def _preview_project_key(self, forest, snapshot):
        settings = tuple(
            sorted(
                (str(key), repr(value))
                for key, value in snapshot.module_settings.items()
            )
        )
        return (
            snapshot.project_id,
            str(getattr(forest, "cache_id", "") or ""),
            str(getattr(forest, "scan_dir", "")),
            len(getattr(forest, "files", ()) or ()),
            len(getattr(forest, "xml_files", ()) or ()),
            snapshot.save_mode,
            snapshot.xml_patch_mode,
            snapshot.project_name,
            settings,
        )

    def _draft_preview_key(self, draft, forest, snapshot):
        return (
            "draft",
            self._preview_project_key(forest, snapshot),
            path_key(draft.path),
            bool(draft.is_new),
            int(draft.revision),
            len(draft.original_text or ""),
            len(draft.current_text or ""),
        )

    def _asset_preview_key(self, draft, forest, snapshot):
        return (
            "asset",
            self._preview_project_key(forest, snapshot),
            path_key(draft.path),
            bool(draft.is_new),
            int(draft.revision),
            draft.original_sha1,
            draft.current_sha1,
        )

    def _resolved_preview_key(self, draft, forest, snapshot):
        return (
            "resolved",
            self._preview_project_key(forest, snapshot),
            str(draft.tab_id),
            path_key(draft.root_path),
            int(draft.revision),
            len(draft.original_text or ""),
            len(draft.current_text or ""),
            len(draft.line_sources or ()),
        )

    @staticmethod
    def _preview_items_size(items):
        total = 0
        for item in items:
            total += len(getattr(item, "text", "") or "")
            for attr in ("draft", "resolved"):
                draft = getattr(item, attr, None)
                if draft is None:
                    continue
                for field in (
                    "original_text",
                    "current_text",
                    "saved_text",
                ):
                    total += len(getattr(draft, field, "") or "")
            generated = getattr(item, "generated", None)
            if generated is None:
                continue
            total += len(getattr(generated, "text", "") or "")
            for file in (getattr(generated, "files", ()) or ()):
                total += len(getattr(file, "text", "") or "")
                total += len(getattr(file, "data", b"") or b"")
        return total

    def _cached_preview_items(self, key, factory):
        with self._preview_lock:
            cached = self._preview_cache.get(key)
            if cached is not None:
                self._preview_cache.move_to_end(key)
                return list(cached)

        items = tuple(factory())
        size = self._preview_items_size(items)
        if size > self.PREVIEW_CACHE_MAX_CHARS:
            # Один такой предпросмотр не должен вытеснять весь кэш и
            # занимать гигабайты; отдаём его без кэширования.
            return list(items)

        with self._preview_lock:
            old = self._preview_cache.pop(key, None)
            if old is not None:
                self._preview_cache_chars -= self._preview_cache_sizes.pop(
                    key,
                    0,
                )
            self._preview_cache[key] = items
            self._preview_cache_sizes[key] = size
            self._preview_cache_chars += size
            self._preview_cache.move_to_end(key)
            while (
                len(self._preview_cache) > self.PREVIEW_CACHE_SIZE
                or (
                    self._preview_cache_chars
                    > self.PREVIEW_CACHE_MAX_CHARS
                    and len(self._preview_cache) > 1
                )
            ):
                evicted, _value = self._preview_cache.popitem(last=False)
                self._preview_cache_chars -= self._preview_cache_sizes.pop(
                    evicted,
                    0,
                )
            if self._preview_cache_chars < 0:
                self._preview_cache_chars = 0
        return list(items)

    # -------------------------------------------------- предпросмотр патча
    def build(
        self,
        patch,
        forest,
        snapshot,
        log=None,
    ) -> ProjectPatchPreview:
        """Собрать предпросмотр по снимку в выбранном режиме проекта."""
        mode = normalize_save_mode(snapshot.save_mode or "root")
        if mode == "root":
            return self._build_preview_root(
                patch,
                forest,
                snapshot,
                mode,
                log=log,
            )
        return self._build_preview_per_file(
            patch,
            forest,
            snapshot,
            mode,
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

        game_root = game_root_from_scan_dir(scan_dir)
        if game_root is None:
            raise ProjectError("Не определён каталог gamedata")

        try:
            return self.asset_registry.relative_for_path(game_root, path)
        except ProjectAssetTypeError as exc:
            raise ProjectError(str(exc))

    def _asset_preview_item(
        self,
        project_name,
        forest,
        draft,
        title,
        settings,
    ):
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
                rollback_paths=(Path(draft.path),),
            )

        save_mode = "override"
        setting_key = getattr(asset_type, "save_mode_setting", "")
        if setting_key:
            save_mode = str(
                settings.get(setting_key, "override")
                or "override"
            ).strip().lower()
        allowed_modes = tuple(
            getattr(asset_type, "save_modes", ()) or ("override",)
        )
        if save_mode not in allowed_modes:
            return PreviewItem(
                title=title,
                source_path=Path(draft.path),
                plan=None,
                generated=None,
                error=(
                    "Неизвестный режим записи ресурса %s: %s"
                    % (asset_type.label, save_mode)
                ),
                kind="asset",
                draft=draft,
                notes=("Допустимые режимы: %s" % ", ".join(allowed_modes),),
                rollback_paths=(Path(draft.path),),
            )

        patch_rel = Path("gamedata") / relative
        intent = PatchIntent(
            kind="asset",
            source_path=Path(draft.path),
            patch_mod="ACE Patch - %s" % project_name,
            relative_path=relative,
            patch_root=Path(getattr(forest, "scan_dir", "")).parent,
            vfs_root=asset_type.vfs_root,
            asset_save_mode=save_mode,
            asset_data=bytes(draft.current_bytes),
            changes=(
                AssetChange(
                    kind="asset",
                    path=Path(draft.path),
                    description="%s: заменить файл целиком" % asset_type.label,
                ),
            ),
        )
        snapshot = PatchSnapshot(
            forest=forest,
            snapshot_id=str(getattr(forest, "cache_id", "") or ""),
        )
        try:
            bundle = self._file_patch_orchestrator.build(
                intent,
                snapshot,
            )
        except PatchPlanningError as exc:
            return PreviewItem(
                title=title,
                source_path=Path(draft.path),
                plan=None,
                generated=None,
                error=str(exc),
                kind="asset",
                draft=draft,
                rollback_paths=(Path(draft.path),),
            )
        if bundle.generated is None:
            return PreviewItem(
                title=title,
                source_path=Path(draft.path),
                plan=bundle.plan,
                generated=None,
                error="Патч ресурса не сгенерирован",
                kind="asset",
                draft=draft,
                rollback_paths=(Path(draft.path),),
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

        return PreviewItem(
            title=title,
            source_path=Path(draft.path),
            plan=bundle.plan,
            generated=bundle.generated,
            error=(
                "" if not bundle.plan.has_errors
                else "План содержит ошибки"
            ),
            kind="asset",
            draft=None,
            notes=(
                "Тип: %s" % asset_type.label,
                "Режим: %s" % save_mode,
                "Ресурс: %s" % patch_rel,
                "Размер: %d байт" % len(draft.current_bytes),
                "Новый файл" if draft.is_new else "Замена существующего файла",
            ) + tuple(reader_notes),
            text=view_text or (
                "Двоичный файл: %d байт" % len(draft.current_bytes)
            ),
            rollback_paths=(Path(draft.path),),
        )

    def _new_file_preview(self, project_name, forest, draft, title):
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
                rollback_paths=(Path(draft.path),),
            )

        intent = PatchIntent(
            kind="new_file",
            source_path=Path(draft.path),
            patch_mod="ACE Patch - %s" % project_name,
            after_text=str(draft.current_text),
            relative_path=relative_path,
        )
        snapshot = PatchSnapshot(
            forest=forest,
            snapshot_id=str(getattr(forest, "cache_id", "") or ""),
        )
        try:
            bundle = self._file_patch_orchestrator.build(
                intent,
                snapshot,
            )
        except PatchPlanningError as exc:
            return PreviewItem(
                title=title,
                source_path=Path(draft.path),
                plan=None,
                generated=None,
                error=str(exc),
                kind="new_file",
                draft=draft,
                rollback_paths=(Path(draft.path),),
            )
        if bundle.generated is None:
            return PreviewItem(
                title=title,
                source_path=Path(draft.path),
                plan=bundle.plan,
                generated=None,
                error="Патч нового файла не сгенерирован",
                kind="new_file",
                draft=draft,
                rollback_paths=(Path(draft.path),),
            )
        return PreviewItem(
            title=title,
            source_path=Path(draft.path),
            plan=bundle.plan,
            generated=bundle.generated,
            error=(
                "" if not bundle.plan.has_errors
                else "План содержит ошибки"
            ),
            kind="new_file",
            draft=draft,
            notes=(
                "Новый файл: %s"
                % (Path("gamedata") / "configs" / relative_path),
            ),
            text=str(draft.current_text),
            rollback_paths=(Path(draft.path),),
        )

    def _build_preview_per_file(
        self,
        patch,
        forest,
        snapshot,
        mode,
        log=None,
        generate=True,
        verify_resolver=True,
    ) -> ProjectPatchPreview:
        project = snapshot.project
        raw_drafts = list(snapshot.drafts)
        asset_drafts = list(snapshot.assets)
        resolved_drafts = list(snapshot.resolved_drafts)

        # Снимок для XML-целей патча: индекс include должен строиться
        # по тем же черновикам, что и сами патчи, а не по диску.
        overlay = ProjectOverlay(
            source_files={
                Path(draft.path): str(draft.current_text)
                for draft in raw_drafts
            },
            resolved_edits={
                Path(draft.root_path): (
                    str(draft.original_text),
                    str(draft.current_text),
                    tuple(draft.line_sources or ()),
                )
                for draft in resolved_drafts
            },
            new_files=frozenset(
                Path(draft.path)
                for draft in raw_drafts
                if draft.is_new
            ),
        )

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

            rollback_paths = (
                (Path(draft.path),) if draft is not None else ()
            )
            rollback_resolved_ids = ()
            if resolved is not None:
                rollback_resolved_ids = (
                    resolved.tab_id
                    or resolved_key("", resolved.root_path),
                )

            if log:
                log("Проект: подготовка %s" % title)

            if draft is not None and draft.is_new:
                output.append(
                    self._new_file_preview(
                        snapshot.project_name,
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
                        rollback_paths=rollback_paths,
                        rollback_resolved_ids=rollback_resolved_ids,
                    )
                )
                return

            is_xml = suffix_low == ".xml"
            override_mode = mode == "override"
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
                            rollback_paths=rollback_paths,
                            rollback_resolved_ids=rollback_resolved_ids,
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
                            rollback_paths=rollback_paths,
                            rollback_resolved_ids=rollback_resolved_ids,
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
                patch_mod="ACE Patch - %s" % snapshot.project_name,
                suffix=suffix,
                project_tag=snapshot.project_name,
                allow_delete_section=True,
                allow_delete_xml_element=True,
                xml_patch_mode=snapshot.xml_patch_mode,
                snapshot_id=str(getattr(forest, "cache_id", "") or ""),
            )

            if kind == "file" and not is_xml and _is_mod_ltx(source_path):
                # mod_*.ltx — готовый DLTX-патч. Его нельзя снова
                # превращать в дельту: правка записывается полной
                # заменой самого файла.
                try:
                    result = patch.plan_ltx_patch(
                        source_path,
                        original_text,
                        current_text,
                        mode="override",
                        forest=forest,
                        options=options,
                        verify_resolver=False,
                    )
                    plan = result.plan
                    generated = result.generated
                    output.append(
                        PreviewItem(
                            title=title,
                            source_path=Path(source_path),
                            plan=plan,
                            generated=generated,
                            error=(
                                "" if plan.can_generate
                                else "План содержит ошибки"
                            ),
                            kind="mod_file",
                            draft=draft,
                            notes=tuple(notes) + (
                                "Мод-файл: полная замена файла",
                            ),
                            text=str(current_text),
                            rollback_paths=rollback_paths,
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
                            kind="mod_file",
                            draft=draft,
                            notes=notes,
                            rollback_paths=rollback_paths,
                        )
                    )
                return

            try:
                # Единый вход core: mode/format-специфичная диспетчеризация
                # живёт в LtxPatchService/XmlPatchService, а не в UI.
                line_sources = (
                    resolved.line_sources
                    if kind == "resolved" and resolved is not None
                    else ()
                )
                if is_xml:
                    xml_setting = snapshot.xml_patch_mode.lower()
                    if xml_setting == "xml_override":
                        xml_mode = "full_copy"
                    elif xml_setting == "xml":
                        xml_mode = "xml"
                    else:
                        xml_mode = "dxml_lua"
                    # Planner читает режим из options; передаём ему тот
                    # же режим, что выбрали для сервиса.
                    options = replace(
                        options,
                        xml_patch_mode=xml_mode,
                    )
                    result = patch.plan_xml_patch(
                        source_path,
                        original_text,
                        current_text,
                        mode=xml_mode,
                        line_sources=line_sources,
                        forest=forest,
                        options=options,
                        project_mode=mode,
                        overlay=overlay,
                    )
                else:
                    result = patch.plan_ltx_patch(
                        source_path,
                        original_text,
                        current_text,
                        mode=mode,
                        line_sources=line_sources,
                        forest=forest,
                        options=options,
                        resolved=(
                            kind == "resolved" and resolved is not None
                        ),
                        analysis=resolved_analysis,
                        # В Root-режиме поштучный патч будет заменён
                        # объединённым, поэтому полный проход Resolver4
                        # здесь не нужен. XML-ветка не затрагивается:
                        # её файлы попадают в патч как есть.
                        verify_resolver=verify_resolver,
                    )

                plan = result.plan
                notes = notes + tuple(result.notes or ())
                pre_generated = result.generated
                fork_result = result if result.references else None
                if fork_result is not None:
                    notes = notes + tuple(
                        "Ключ: %s -> %s" % (key.old_key, key.new_key)
                        for key in (result.keys or ())
                    ) + tuple(
                        "Ссылка: %s" % (
                            getattr(ref.path, "name", ref.path)
                        )
                        for ref in result.references[:20]
                    )

                if (
                    not is_xml
                    and mode == "on_file"
                    and plan.target.root_path is not None
                ):
                    notes = notes + (
                        "On-file: отдельный патч на файл; корень — %s"
                        % Path(plan.target.root_path).name,
                    )

                if pre_generated is not None:
                    generated = pre_generated
                elif plan.can_generate and generate:
                    generated = patch.generate_patch(plan)
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
                        plans=tuple(getattr(result, "plans", ()) or ()),
                        rollback_paths=rollback_paths,
                        rollback_resolved_ids=rollback_resolved_ids,
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
                        rollback_paths=rollback_paths,
                        rollback_resolved_ids=rollback_resolved_ids,
                    )
                )

        index = 0
        for draft in raw_drafts:
            index += 1
            key = self._draft_preview_key(draft, forest, snapshot)
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
            key = self._asset_preview_key(draft, forest, snapshot)
            items.extend(
                self._cached_preview_items(
                    key,
                    lambda draft=draft: (
                        self._asset_preview_item(
                            snapshot.project_name,
                            forest,
                            draft,
                            draft.path.name,
                            snapshot.module_settings,
                        ),
                    ),
                )
            )

        for draft in resolved_drafts:
            index += 1
            key = self._resolved_preview_key(draft, forest, snapshot)
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
                key = (path_key(item.source_path), item.text)
                if key in seen_scripts:
                    continue
                seen_scripts.add(key)
            deduped.append(item)

        return ProjectPatchPreview(project=project, items=tuple(deduped))

    def _build_preview_root(
        self,
        patch,
        forest,
        snapshot,
        mode,
        log=None,
    ):
        """Один патч на корень загрузки; XML — один пакет на проект."""
        base = self._build_preview_per_file(
            patch,
            forest,
            snapshot,
            mode,
            log=log,
            generate=False,
            # Поштучные DLTX-патчи в Root-режиме не записываются:
            # итоговый файл собирается ниже и проверяется один раз.
            verify_resolver=False,
        )

        items: List[PreviewItem] = []
        groups = {}
        # DLTX-планы, которые XML-форк собрал для чужого корня. Они
        # должны попасть в общий патч этого корня, иначе два элемента
        # предпросмотра создадут один и тот же файл.
        extra_plans = {}
        extra_notes = {}
        extra_rollback_paths = {}
        extra_rollback_resolved = {}

        for item in base.items:
            if (
                item.kind == "asset"
                or item.kind == "new_file"
                or item.kind == "mod_file"
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
                groups.setdefault("xml:project", []).append(item)
                for plan in item.plans:
                    if (
                        plan.is_xml
                        or plan.target.root_path is None
                        or not plan.changes
                    ):
                        continue
                    root_key = str(Path(plan.target.root_path)).lower()
                    extra_plans.setdefault(root_key, []).append(plan)
                    extra_notes.setdefault(root_key, []).extend(item.notes)
                    extra_rollback_paths.setdefault(
                        root_key,
                        [],
                    ).extend(item.rollback_paths)
                    extra_rollback_resolved.setdefault(
                        root_key,
                        [],
                    ).extend(item.rollback_resolved_ids)
                continue

            key = str(item.plan.target.root_path).lower()
            groups.setdefault(key, []).append(item)

        # Корень может быть затронут только ссылками из XML-форка.
        for key in extra_plans:
            groups.setdefault(key, [])

        for key in sorted(groups):
            group = groups[key]
            is_xml = key == "xml:project"
            if is_xml:
                root_path = group[0].source_path
                title = "XML проекта (Root)"
            else:
                plan = next(
                    (item.plan for item in group if item.plan is not None),
                    None,
                )
                if plan is None:
                    plan = extra_plans[key][0]
                root_path = plan.target.root_path
                title = "%s (Root)" % Path(root_path).name

            notes = ["Режим: Root", "Файлов: %d" % len(group)]
            for item in group:
                notes.extend(item.notes)
            for note in extra_notes.get(key, ()):
                if note not in notes:
                    notes.append(note)

            rollback_paths = []
            rollback_resolved_ids = []
            for member in group:
                rollback_paths.extend(member.rollback_paths)
                rollback_resolved_ids.extend(
                    member.rollback_resolved_ids
                )
            rollback_paths.extend(extra_rollback_paths.get(key, ()))
            rollback_resolved_ids.extend(
                extra_rollback_resolved.get(key, ())
            )
            rollback_paths = tuple(dict.fromkeys(rollback_paths))
            rollback_resolved_ids = tuple(
                dict.fromkeys(rollback_resolved_ids)
            )

            try:
                if is_xml:
                    merged, generated = merge_xml_generated(
                        patch,
                        [
                            (item.plan, item.generated, item.source_path)
                            for item in group
                        ],
                    )
                else:
                    plans = [
                        item.plan for item in group
                        if item.plan is not None and item.plan.changes
                    ]
                    plans.extend(extra_plans.get(key, ()))
                    merged = merge_patch_plans(
                        plans,
                        patch_mod=(
                            group[0].plan.patch_mod
                            if group and group[0].plan is not None
                            else plans[0].patch_mod
                        ),
                    )
                    generated = (
                        patch.generate_patch(merged)
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
                        rollback_paths=rollback_paths,
                        rollback_resolved_ids=rollback_resolved_ids,
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
                        rollback_paths=rollback_paths,
                        rollback_resolved_ids=rollback_resolved_ids,
                    )
                )

        return ProjectPatchPreview(project=base.project, items=tuple(items))

    # -------------------------------------------------- служебное
