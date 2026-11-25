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
from functools import wraps
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from core.api.vfs import read_text_safe, read_xml_text

from sdk.contracts import ProjectOverlay
from sdk.features.projects import SavedDraft

from app.documents import collapse_duplicated_text

from .assets import ProjectAssetRegistry
from .readers import ProjectFileReaderRegistry
from .drafts import DraftStore
from .overlay import OverlayBuilder
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


from .models import (
    Project,
    ProjectAssetDraft,
    ProjectDraft,
    ProjectError,
    ProjectInfo,
    ProjectResolvedDraft,
    normalize_save_mode,
    resolved_key,
)

log = logging.getLogger(__name__)

# Отличает отсутствующий ключ настройки от сохранённого значения None.
_MISSING_SETTING = object()


def _serialized_lifecycle(method):
    """Сериализовать создание/загрузку/закрытие/удаление проекта."""
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lifecycle_lock:
            return method(self, *args, **kwargs)

    return wrapper


class ProjectService:
    """Хранилище проектов и черновиков."""

    META_NAME = PROJECT_META_NAME
    DRAFTS_DIR = PROJECT_DRAFTS_DIR
    RESOLVED_DIR = PROJECT_RESOLVED_DIR
    ASSETS_DIR = PROJECT_ASSETS_DIR
    ROOTS_NAME = "project_roots.json"
    # Сессия редактора: какие вкладки были открыты при прошлом закрытии.
    TABS_NAME = PROJECT_TABS_NAME
    TABS_VERSION = 1

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
        self._roots_lock = threading.RLock()
        self._project_roots = self._load_project_roots()
        self._lifecycle_lock = threading.RLock()
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
        self.preview_builder = self._create_preview_builder()
        # Сериализует запись файлов черновиков: фоновая запись текста и
        # автосохранение не должны одновременно писать один файл.
        self._write_lock = threading.RLock()
        self.draft_store = DraftStore(self._write_lock)
        # Защищает словари проекта и кэш overlay от чтения во время
        # фонового предпросмотра/автосохранения.
        self._state_lock = threading.RLock()
        # Эпоха автосохранения: close/flush/смена проекта запрещают
        # старым фоновым проходам писать после этой точки.
        self._autosave_epoch = 0
        # Файлы удалённых черновиков, которые не удалось убрать сразу:
        # _write_lock занят фоновой записью. Очередь разбирается следующим
        # autosave/flush/load в фоновом потоке, UI не ждёт запись.
        self._pending_deletes = {}
        self.overlay_builder = OverlayBuilder(self._state_lock)
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
        self.overlay_builder.clear()

    def project_overlay(self) -> ProjectOverlay:
        """Снимок правок проекта. Собирается один раз на состояние."""
        return self.overlay_builder.build(
            self.active,
            is_current=lambda project: self.active is project,
        )

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
        with self._roots_lock:
            data = [
                str(root)
                for root in self._project_roots
                if root != self.base_dir
            ]
            try:
                write_json_atomic(
                    self.base_dir / self.ROOTS_NAME,
                    data,
                )
            except OSError:
                pass

    def _register_project_root(self, root) -> bool:
        try:
            root = Path(root).expanduser().resolve()
        except (OSError, TypeError, ValueError):
            return False
        with self._roots_lock:
            if root in self._project_roots:
                return False
            self._project_roots.append(root)
            self._save_project_roots()
            return True

    def _unregister_empty_root(self, root) -> bool:
        """Убрать root из реестра, если в нём не осталось проектов."""
        try:
            root = Path(root).expanduser().resolve()
        except (OSError, TypeError, ValueError):
            return False
        with self._roots_lock:
            if root == self.base_dir or root not in self._project_roots:
                return False
            try:
                has_projects = any(
                    (child / self.META_NAME).is_file()
                    for child in root.iterdir()
                    if child.is_dir()
                )
            except OSError:
                has_projects = True
            if has_projects:
                return False
            self._project_roots.remove(root)
            self._save_project_roots()
            return True

    def _project_dir(self, project_id: str) -> Path:
        with self._roots_lock:
            roots = tuple(self._project_roots)
        for root in roots:
            candidate = root / project_id
            if (candidate / self.META_NAME).is_file():
                return candidate
        return self.base_dir / project_id

    def suggest_project_dir(self, name: str) -> Path:
        """Предложить каталог нового проекта по его названию."""
        return self.base_dir / self._make_project_id(name)

    def _cleanup_failed_create(self, root_dir, created_dir):
        """Убрать следы неудачного создания нового проекта."""
        for name in (self.DRAFTS_DIR, self.RESOLVED_DIR, self.ASSETS_DIR):
            try:
                shutil.rmtree(root_dir / name)
            except OSError:
                pass
        try:
            (root_dir / self.META_NAME).unlink()
        except OSError:
            pass
        if created_dir:
            try:
                shutil.rmtree(root_dir)
            except OSError:
                pass

    # -------------------------------------------------- создание / загрузка
    @_serialized_lifecycle
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
            project_id = root_dir.name
            create_dir = not root_dir.exists()
        else:
            project_id = self._make_project_id(name)
            root_dir = self._project_dir(project_id)
            if root_dir.exists():
                raise ProjectError(
                    "Каталог проекта уже существует: %s" % root_dir
                )
            create_dir = True

        previous = self.active
        # Инициализированы до try: ошибка mkdir/register не должна
        # маскироваться UnboundLocalError в обработчике.
        prepared = False
        root_added = False
        try:
            if create_dir:
                try:
                    root_dir.mkdir(
                        parents=True,
                        exist_ok=bool(project_dir),
                    )
                except OSError as exc:
                    raise ProjectError(
                        "Не удалось создать каталог проекта: %s" % exc
                    )

            prepared = True
            root_added = self._register_project_root(root_dir.parent)
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
            # Сначала фиксируем новый проект на диске. Старый активный
            # проект не закрываем, пока новый не готов: ошибка записи
            # оставляет пользователя в прежнем проекте.
            self.repository.write_meta(project)

            if previous is not None:
                self.close()

            self.invalidate_preview_cache()
            self._resolved_draft_files.clear()
            self._clear_overlay_cache()
            try:
                self.session.activate(project)
            except Exception:
                if previous is not None:
                    try:
                        self.load(previous.id)
                    except Exception:
                        log.exception(
                            "Проект: не удалось восстановить %s после "
                            "ошибки активации нового проекта",
                            previous.id,
                        )
                raise
            return project
        except Exception:
            if prepared:
                self._cleanup_failed_create(root_dir, create_dir)
            if root_added:
                self._unregister_empty_root(root_dir.parent)
            raise

    @_serialized_lifecycle
    def load(self, project_id: str) -> Project:
        # Отложенные удаления могли остаться от прошлого проекта: до
        # чтения черновиков убираем их, чтобы не воскресить старый файл.
        self._drain_pending_deletes()
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

    @_serialized_lifecycle
    def list_projects(self, should_stop=None) -> List[ProjectInfo]:
        """Список проектов; сканирование не пересекается с create/load/delete."""
        result: List[ProjectInfo] = []
        seen = set()
        entries = []

        def stopped():
            return bool(should_stop is not None and should_stop())

        with self._roots_lock:
            # Снимок корней и обход каталогов — под одним lock. Чтение
            # project.json идёт после него: список не держит roots-lock
            # на время тяжёлого чтения.
            for root in tuple(self._project_roots):
                if stopped():
                    return []
                if not root.is_dir():
                    continue
                for entry in root.iterdir():
                    if stopped():
                        return []
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
            if stopped():
                return []
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

    @_serialized_lifecycle
    def close(self) -> None:
        if self.active is None:
            self._drain_pending_deletes()
            self.invalidate_preview_cache()
            self._clear_overlay_cache()
            return
        # Запрещаем фоновым проходам писать старый снимок; их запись
        # будет отвергнута, а flush ниже запишет актуальный текст.
        self.invalidate_autosave()
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

    @_serialized_lifecycle
    def delete_active_project(self, expected_project=None) -> bool:
        """Удалить активный проект со всеми черновиками.

        Это разрушительная операция: проект не сохраняется и не переносится
        в корзину. Каталог удаляется только если он находится внутри
        хранилища проектов. ``expected_project`` не даёт фоновой задаче
        удалить уже сменившийся проект.
        """
        project = self._require_active()
        if (
            expected_project is not None
            and project is not expected_project
        ):
            raise ProjectError(
                "Активный проект изменился: удаление отменено"
            )
        root = Path(project.root_dir)
        target = root.resolve()

        inside = False
        with self._roots_lock:
            roots = tuple(self._project_roots)
        for allowed in roots:
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
        self.invalidate_autosave()

        # Удаление каталога не должно пересечься с фоновой записью
        # черновика. autosave проверяет эпоху под этим же lock, поэтому
        # после invalidation старый снимок уже не запишется.
        with self._write_lock:
            if not target.exists():
                self.session.deactivate()
                self._resolved_draft_files.clear()
                return True

            try:
                shutil.rmtree(str(target))
            except OSError as exc:
                raise ProjectError(
                    "Не удалось удалить проект: %s" % exc
                )

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
            self._cancel_deferred_delete_locked(
                self.draft_store.text_target(project.root_dir, path)
            )
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
                # Черновик удалён или изменён после подготовки снимка:
                # не воскрешаем его старым текстом.
                if draft is None or int(draft.revision) != int(revision):
                    return False
                self.draft_store.write_payload(target, payload)
                return True

    def mark_draft_saved(self, path, revision, saved_at=None) -> bool:
        """Зафиксировать успешную фоновую запись в памяти проекта."""
        with self._state_lock:
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
        with self._state_lock:
            self._cancel_deferred_delete_locked(
                self.draft_store.text_target(project.root_dir, draft.path)
            )
            project.drafts[self.path_key(draft.path)] = draft
            self._clear_overlay_cache()
        self._invalidate_preview_cache(draft.path)
        self._write_draft(project, draft)
        return draft

    def autosave(
        self,
        expected_project=None,
        should_stop=None,
    ) -> List[SavedDraft]:
        """Записать грязные черновики.

        ``expected_project`` фиксирует проект, для которого запущен проход:
        если активный проект сменился, проход ничего не делает. ``epoch``
        защищает от записи старого снимка после close/flush или смены
        проекта. ``should_stop`` — кооперативная отмена.
        """
        try:
            return self._autosave_pass(
                expected_project=expected_project,
                should_stop=should_stop,
            )
        finally:
            # Удаления, отложенные из UI, разбираются здесь: write-lock
            # уже свободен или освободится сразу после прохода.
            try:
                self._drain_pending_deletes()
            except Exception:
                log.exception(
                    "Проект: не удалось убрать отложенные файлы черновиков"
                )

    def _autosave_pass(
        self,
        expected_project=None,
        should_stop=None,
    ) -> List[SavedDraft]:
        saved: List[SavedDraft] = []
        self._drain_pending_deletes()

        # Снимок списков — под state-lock. Сама запись идёт под
        # write-lock, чтобы не держать блокировку состояния на время
        # сериализации десятков мегабайт (иначе UI встанет).
        with self._state_lock:
            project = self._require_active()
            if (
                expected_project is not None
                and project is not expected_project
            ):
                return []
            epoch = self._autosave_epoch
            drafts = [
                (draft, int(draft.revision))
                for draft in project.drafts.values()
                if draft.is_dirty
            ]
            assets = [
                (draft, int(draft.revision))
                for draft in project.assets.values()
                if draft.is_dirty
            ]
            resolved = [
                (draft, int(draft.revision))
                for draft in project.resolved_drafts.values()
                if draft.is_dirty
            ]

        def stopped():
            return bool(should_stop is not None and should_stop())

        for draft, revision in drafts:
            if stopped():
                break
            text = str(draft.current_text or "")
            saved_at = time.time()
            key = self.path_key(draft.path)
            target = self.draft_store.text_target(
                project.root_dir,
                draft.path,
            )
            payload = self.draft_store.build_text_payload(
                draft,
                saved_text=text,
                saved_at=saved_at,
            )
            with self._write_lock:
                with self._state_lock:
                    if not self._autosave_can_write(project, epoch):
                        return saved
                    if project.drafts.get(key) is not draft:
                        continue
                    if int(draft.revision) != revision:
                        # Черновик изменился после снимка: не пишем
                        # устаревший текст.
                        continue
                self.draft_store.write_payload(target, payload)
            with self._state_lock:
                if project.drafts.get(key) is not draft:
                    # Черновик удалён во время записи: не публикуем
                    # событие о восстановленном файле.
                    continue
                if draft.current_text == text:
                    draft.saved_text = text
                    draft.saved_at = saved_at
                saved.append(
                    SavedDraft(
                        kind="file",
                        key=key,
                        path=draft.path,
                        revision=int(draft.revision),
                        size=len(text),
                    )
                )

        for draft, revision in assets:
            if stopped():
                break
            data = bytes(draft.current_bytes)
            saved_at = time.time()
            key = self.path_key(draft.path)
            with self._write_lock:
                with self._state_lock:
                    if not self._autosave_can_write(project, epoch):
                        return saved
                    if project.assets.get(key) is not draft:
                        continue
                    if int(draft.revision) != revision:
                        continue
                self._write_asset_draft(
                    project,
                    draft,
                    saved_bytes=data,
                    saved_at=saved_at,
                )
            with self._state_lock:
                if project.assets.get(key) is not draft:
                    continue
                if draft.current_bytes == data:
                    draft.saved_bytes = data
                    draft.saved_at = saved_at
                saved.append(
                    SavedDraft(
                        kind="asset",
                        key=key,
                        path=draft.path,
                        revision=int(draft.revision),
                        size=len(data),
                    )
                )

        for draft, revision in resolved:
            if stopped():
                break
            text = str(draft.current_text or "")
            saved_at = time.time()
            key = self.resolved_key(draft.tab_id, draft.root_path)
            with self._write_lock:
                with self._state_lock:
                    if not self._autosave_can_write(project, epoch):
                        return saved
                    if project.resolved_drafts.get(key) is not draft:
                        continue
                    if int(draft.revision) != revision:
                        continue
                self._write_resolved_draft(
                    project,
                    draft,
                    saved_text=text,
                    saved_at=saved_at,
                )
            with self._state_lock:
                if project.resolved_drafts.get(key) is not draft:
                    continue
                if draft.current_text == text:
                    draft.saved_text = text
                    draft.saved_at = saved_at
                saved.append(
                    SavedDraft(
                        kind="resolved",
                        key=key,
                        path=draft.root_path,
                        revision=int(draft.revision),
                        size=len(text),
                    )
                )

        return saved

    def invalidate_autosave(self) -> None:
        """Запретить фоновым проходам писать после этой точки."""
        with self._state_lock:
            self._autosave_epoch += 1

    def _autosave_can_write(self, project, epoch) -> bool:
        """Проверка под state-lock: проход всё ещё для того проекта."""
        return (
            self._autosave_epoch == epoch
            and self.active is project
        )

    def _queue_deferred_delete(self, target) -> None:
        key = self.path_key(target)
        with self._state_lock:
            self._pending_deletes[key] = Path(target)

    def _cancel_deferred_delete_locked(self, target) -> None:
        # Вызывается под state-lock: новый черновик по тому же пути
        # отменяет отложенное удаление старого файла.
        self._pending_deletes.pop(self.path_key(target), None)

    def _remove_or_defer(self, mutate) -> bool:
        """Удалить черновик сейчас или атомарно отложить удаление.

        ``mutate`` вызывается под ``state-lock``: он убирает черновик из
        проекта и возвращает target либо ``None``. Если write-lock занят,
        target попадает в очередь до отпускания state-lock. Поэтому
        параллельный ``update_draft`` либо видит очередь и отменяет её,
        либо выполняется до remove и сам становится объектом удаления —
        окна, где новый черновик остаётся без защиты, нет.
        """
        if self._write_lock.acquire(blocking=False):
            try:
                with self._state_lock:
                    target = mutate()
                if target is None:
                    return False
                self.draft_store.delete_target(target)
                return True
            finally:
                self._write_lock.release()

        with self._state_lock:
            target = mutate()
            if target is None:
                return False
            self._pending_deletes[self.path_key(target)] = Path(target)
        return True

    def _drain_pending_deletes(self) -> None:
        """Убрать отложенные файлы под write-lock (фоновый поток)."""
        while True:
            with self._write_lock:
                with self._state_lock:
                    if not self._pending_deletes:
                        return
                    targets = list(self._pending_deletes.values())
                    self._pending_deletes.clear()
                for target in targets:
                    self.draft_store.delete_target(target)

    def has_pending_cleanup(self) -> bool:
        with self._state_lock:
            return bool(self._pending_deletes)

    def flush(self) -> List[SavedDraft]:
        if self.active is None:
            self._drain_pending_deletes()
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

        def mutate():
            if self.active is not project:
                return None
            draft = project.drafts.pop(key, None)
            if draft is None:
                return None
            self.invalidate_autosave()
            self._clear_overlay_cache()
            return self.draft_store.text_target(project.root_dir, path)

        if not self._remove_or_defer(mutate):
            return False
        self._invalidate_preview_cache(path)
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
            self._cancel_deferred_delete_locked(
                self.draft_store.asset_target(project.root_dir, path)
            )
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

        def mutate():
            if self.active is not project:
                return None
            draft = project.assets.pop(key, None)
            if draft is None:
                return None
            self.invalidate_autosave()
            return self.draft_store.asset_target(project.root_dir, path)

        if not self._remove_or_defer(mutate):
            return False
        self._invalidate_preview_cache(path)
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
                self._cancel_deferred_delete_locked(
                    self.draft_store.resolved_target(
                        project.root_dir,
                        draft,
                        existing=self._resolved_draft_files.get(key),
                    )
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

        def mutate():
            if self.active is not project:
                return None
            draft = project.resolved_drafts.pop(key, None)
            if draft is None:
                return None
            self.invalidate_autosave()
            self._clear_overlay_cache()
            target = self._resolved_draft_files.pop(key, None)
            if target is None:
                target = self.draft_store.resolved_target(
                    project.root_dir,
                    draft,
                )
            return target

        return self._remove_or_defer(mutate)

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

    # -------------------------------------------------- предпросмотр
    def _create_preview_builder(self):
        # Локальный импорт разрывает цикл app.projects <-> app.patching.
        from app.patching.preview import ProjectPreviewBuilder

        return ProjectPreviewBuilder(
            self.asset_registry,
            self.file_readers,
        )

    def invalidate_preview_cache(self):
        """Сбросить кэш предпросмотра (например, после смены типов)."""
        self.preview_builder.invalidate()

    def _invalidate_preview_cache(self, path=None):
        self.preview_builder.invalidate(path)

    def build_preview(
        self,
        patch_services,
        forest,
        log=None,
        expected_project=None,
    ):
        """Собрать предпросмотр по снимку активного проекта.

        ``patch_services`` — узкий патч-компонент, а не широкий
        композиционный объект. ``expected_project`` — проект, для которого
        запущен фон: если активный проект сменился, возвращается ``None``.
        """
        from app.patching.preview import PreviewSnapshot

        project, state = self.session.snapshot()
        if project is None or state is None:
            if expected_project is not None:
                return None
            raise ProjectError("Проект не создан")
        if expected_project is not None and project is not expected_project:
            return None
        with self._state_lock:
            snapshot = PreviewSnapshot.capture(
                project,
                save_mode=state.save_mode,
                xml_patch_mode=state.xml_patch_mode,
                module_settings=state.module_settings,
            )
        return self.preview_builder.build(
            patch_services,
            forest,
            snapshot,
            log=log,
        )

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
                # Текст изменился без роста ревизии: кэш overlay
                # больше не соответствует состоянию.
                self._clear_overlay_cache()
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
