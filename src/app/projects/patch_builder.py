# -*- coding: utf-8 -*-
"""Построение предпросмотра патча проекта.

Вынесено из ProjectService. Использует единые входы core, когда они
доступны, и не знает про Qt.
"""

from __future__ import annotations

import hashlib
import os
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path
from typing import List, Tuple

from core import (
    GeneratedPatch,
    PatchFile,
    PatchIssue,
    PatchOptions,
    PatchPlan,
    PatchTarget,
    VerificationCheck,
    VerificationResult,
    analyze_multiline_edit,
    merge_patch_plans,
)

from .assets import ProjectAssetTypeError
from .models import (
    AssetChange,
    PreviewItem,
    ProjectDraft,
    ProjectAssetDraft,
    ProjectError,
    ProjectPatchPreview,
    ProjectResolvedDraft,
)
from .readers import FileReaderError


class ProjectPreviewMixin:
    """Preview-часть ProjectService. Наследуется сервисом проекта."""

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
                # Единый вход core: mode/format-специфичная диспетчеризация
                # живёт в LtxPatchService/XmlPatchService, а не в UI.
                line_sources = (
                    resolved.line_sources
                    if kind == "resolved" and resolved is not None
                    else ()
                )
                if is_xml:
                    xml_setting = project.xml_patch_mode.lower()
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
                    result = services.plan_xml_patch(
                        source_path,
                        original_text,
                        current_text,
                        mode=xml_mode,
                        line_sources=line_sources,
                        forest=forest,
                        options=options,
                        project_mode=self.patch_mode(),
                    )
                else:
                    result = services.plan_ltx_patch(
                        source_path,
                        original_text,
                        current_text,
                        mode=self.patch_mode(),
                        line_sources=line_sources,
                        forest=forest,
                        options=options,
                        resolved=(
                            kind == "resolved" and resolved is not None
                        ),
                        analysis=resolved_analysis,
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
