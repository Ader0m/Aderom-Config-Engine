# -*- coding: utf-8 -*-
"""Единственный владелец текущего VFS-леса и виртуальных файлов проекта.

Платформа и проектный слой не должны independently добавлять/удалять
файлы в ``LtxForest``. Все такие изменения проходят здесь, чтобы после
них можно было один раз опубликовать новое поколение дерева.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path

from core.api.paths import path_key
from core.api.vfs import file_index_signature, normalize_path
from sdk.contracts.forest import ForestRef, unwrap_forest


CACHE_RULES_VERSION = "stage2-rules-1"


@dataclass(frozen=True)
class VfsSnapshot:
    """Неизменяемое состояние VFS, к которому привязаны кэши."""

    id: str
    generation: int
    scan_dir: Path | None
    system_root: Path | None
    files_signature: str
    overlay_revision: str
    profile: str = ""
    mod_order: tuple = ()
    engine_versions: tuple = ()
    rules_version: str = CACHE_RULES_VERSION


class SnapshotService:
    """Строит и хранит текущий снимок VFS.

    Файловый индекс пересчитывается только при явной пересборке или смене
    корней. Смена overlay создаёт новую ревизию снимка, но переиспользует
    уже посчитанную подпись файлов.

    ``profile``, ``mod_order`` и ``engine_versions`` зарезервированы:
    в production они пока пустые до интеграции MO2-профиля и определения
    версий движка. При их заполнении snapshot_id изменится, и кэш
    пересоберётся.
    """

    def __init__(
        self,
        rules_version=CACHE_RULES_VERSION,
        profile="",
        mod_order=(),
        engine_versions=(),
    ):
        self._lock = threading.RLock()
        self._rules_version = str(rules_version or "")
        self._profile = str(profile or "")
        self._mod_order = tuple(str(item) for item in (mod_order or ()))
        self._engine_versions = tuple(engine_versions or ())
        self._snapshot = None

    @property
    def current(self) -> VfsSnapshot | None:
        with self._lock:
            return self._snapshot

    @property
    def snapshot_id(self) -> str:
        snapshot = self.current
        return snapshot.id if snapshot is not None else ""

    def ensure(
        self,
        scan_dir,
        system_root,
        overlay_revision="",
        refresh=False,
    ) -> VfsSnapshot:
        """Вернуть актуальный снимок, при необходимости пересчитав файлы."""
        scan = normalize_path(scan_dir) if scan_dir else None
        system = normalize_path(system_root) if system_root else None
        overlay_revision = str(overlay_revision or "")

        with self._lock:
            previous = self._snapshot
            need_files = (
                bool(refresh)
                or previous is None
                or previous.scan_dir != scan
                or previous.system_root != system
                or not previous.files_signature
            )
            files_signature = previous.files_signature if previous else ""

        # Сканирование каталога не держит лок: иначе UI-поток,
        # запрашивающий overlay, ждал бы фоновую пересборку.
        if need_files:
            files_signature = (
                file_index_signature(scan) if scan is not None else ""
            )

        with self._lock:
            return self._replace(
                scan,
                system,
                files_signature,
                overlay_revision,
            )

    def observe_forest(self, forest, overlay_revision="") -> VfsSnapshot:
        """Построить снимок по уже собранному лесу.

        Пересборка VFS уже просканировала файлы, поэтому повторный обход
        каталога не нужен: подпись считается по ``forest.files`` и
        ``forest.xml_files``.
        """
        scan = getattr(forest, "scan_dir", None)
        system = getattr(forest, "system_root", None)
        scan = normalize_path(scan) if scan else None
        system = normalize_path(system) if system else None
        paths = list(getattr(forest, "files", ()) or ())
        paths.extend(getattr(forest, "xml_files", ()) or ())
        files_signature = (
            file_index_signature(scan, paths=paths)
            if scan is not None
            else ""
        )
        with self._lock:
            return self._replace(
                scan,
                system,
                files_signature,
                str(overlay_revision or ""),
            )

    def update_overlay(self, revision) -> VfsSnapshot:
        """Создать ревизию снимка из-за смены overlay проекта."""
        overlay_revision = str(revision or "")
        with self._lock:
            previous = self._snapshot
            if previous is None:
                return self._replace(
                    None,
                    None,
                    "",
                    overlay_revision,
                )
            return self._replace(
                previous.scan_dir,
                previous.system_root,
                previous.files_signature,
                overlay_revision,
            )

    def reset(self):
        with self._lock:
            self._snapshot = None

    def _replace(
        self,
        scan_dir,
        system_root,
        files_signature,
        overlay_revision,
    ) -> VfsSnapshot:
        digest = hashlib.sha1()
        for value in (
            path_key(scan_dir) if scan_dir else "",
            path_key(system_root) if system_root else "",
            self._profile,
            "\0".join(self._mod_order),
            files_signature,
            overlay_revision,
            repr(self._engine_versions),
            self._rules_version,
        ):
            digest.update(value.encode("utf-8", "replace"))
            digest.update(b"\1")
        snapshot_id = digest.hexdigest()

        previous = self._snapshot
        if previous is not None and previous.id == snapshot_id:
            return previous

        generation = 1 if previous is None else previous.generation + 1
        snapshot = VfsSnapshot(
            id=snapshot_id,
            generation=generation,
            scan_dir=scan_dir,
            system_root=system_root,
            files_signature=files_signature,
            overlay_revision=overlay_revision,
            profile=self._profile,
            mod_order=self._mod_order,
            engine_versions=self._engine_versions,
            rules_version=self._rules_version,
        )
        self._snapshot = snapshot
        return snapshot


class WorkspaceSession:
    def __init__(self, services=None, snapshot_service=None):
        self._services = services
        self._forest = None
        self._forest_ref = None
        self._config_dir = None
        self._generation = 0
        self._snapshot_id = ""
        self._forest_snapshot_id = ""
        self._stale = False
        self._snapshot_service = snapshot_service or SnapshotService()
        self._virtual_paths = set()
        self.last_added_paths = set()
        self.last_removed_paths = set()
        self.last_removed_roots = set()

    # -------------------------------------------------- состояние
    @property
    def forest(self):
        return self._forest

    @property
    def forest_ref(self):
        """Read-only handle для модулей; сырой лес не покидает платформу."""
        return self._forest_ref

    @property
    def config_dir(self):
        return self._config_dir

    @property
    def generation(self):
        return self._generation

    @property
    def snapshot(self):
        return self._snapshot_service.current

    @property
    def snapshot_id(self):
        """Актуальный снимок workspace (включая overlay)."""
        return self._snapshot_id or self._snapshot_service.snapshot_id

    @property
    def forest_snapshot_id(self):
        """Снимок, с которым фактически собран текущий лес."""
        return self._forest_snapshot_id

    @property
    def is_stale(self):
        """Лес собран не для актуальной overlay-ревизии."""
        return self._stale

    def update_overlay(self, revision):
        """Учесть ревизию overlay в снимке. Возвращает True при смене."""
        snapshot = self._snapshot_service.update_overlay(revision)
        changed = snapshot.id != self._snapshot_id
        self._snapshot_id = snapshot.id
        if changed:
            self._generation += 1
        self._refresh_stale()
        return changed

    def _refresh_stale(self):
        forest = self._forest
        if forest is None:
            self._stale = False
            return
        current = self._snapshot_service.current
        if current is None:
            self._stale = False
            return
        forest_overlay = str(
            getattr(forest, "overlay_revision", "") or ""
        )
        if forest_overlay:
            self._stale = forest_overlay != current.overlay_revision
        else:
            self._stale = bool(current.overlay_revision)

    @property
    def virtual_paths(self):
        return self._virtual_paths

    def set_forest(self, forest, config_dir=None):
        ref = forest if isinstance(forest, ForestRef) else None
        forest = unwrap_forest(forest)
        if forest is not self._forest:
            # Новый лес мог быть построен сразу с overlay. Набор
            # виртуальных путей восстановится из ProjectOverlay.new_files
            # при следующем apply_overlay().
            self._virtual_paths.clear()
            if ref is None and forest is not None:
                ref = ForestRef(forest)
            self._forest_ref = ref
        self._forest = forest
        if forest is None:
            self._forest_ref = None
            self._snapshot_id = ""
            self._forest_snapshot_id = ""
            self._stale = False
            self._snapshot_service.reset()
        else:
            if self._forest_ref is None:
                self._forest_ref = ForestRef(forest)
            cache_id = str(getattr(forest, "cache_id", "") or "")
            self._forest_snapshot_id = cache_id
            current = self._snapshot_service.current
            if current is None:
                self._snapshot_id = cache_id
                self._stale = False
            else:
                self._snapshot_id = current.id
                forest_overlay = str(
                    getattr(forest, "overlay_revision", "") or ""
                )
                if forest_overlay:
                    self._stale = (
                        forest_overlay != current.overlay_revision
                    )
                else:
                    self._stale = bool(current.overlay_revision)
        if config_dir is not None:
            self._config_dir = Path(config_dir)
        self._generation += 1
        return forest

    def root_for(self, path):
        """Корень загрузки, к которому относится файл."""
        forest = self._forest
        if forest is None:
            return None
        current = Path(path)
        seen = set()
        while True:
            key = str(current)
            if key in seen:
                return current
            seen.add(key)
            parents = getattr(forest, "node_parent", {}) or {}
            parent = parents.get(current)
            if parent is None:
                parent = parents.get(str(current))
            if parent is None:
                return current
            current = Path(parent)

    def roots_for(self, paths):
        """Уникальные корни, в чьи поддеревья входят заданные файлы.

        Один файл может входить сразу в несколько корней (общий include,
        DLTX-привязка к нескольким mod-файлам), поэтому обход идёт по
        всем родителям, а не только по одному ``node_parent``. Иначе
        правка общего файла инвалидировала бы лишь часть кеша.

        Путь, который не удаётся связать с корнем дерева, не попадает в
        результат: вызывающая сторона обязана в этом случае откатиться к
        полной инвалидации.
        """
        forest = self._forest
        if forest is None:
            return set()

        parents_map = getattr(forest, "include_parents", {}) or {}
        node_parent = getattr(forest, "node_parent", {}) or {}
        forest_roots = {
            Path(root) for root in (getattr(forest, "roots", ()) or ())
        }

        roots = set()
        for path in paths or ():
            stack = [Path(path)]
            seen = set()
            while stack:
                node = stack.pop()
                key = str(node)
                if key in seen:
                    continue
                seen.add(key)

                parents = parents_map.get(node)
                if not parents:
                    parents = parents_map.get(key)
                parents = [Path(parent) for parent in (parents or ())]

                if not parents:
                    parent = node_parent.get(node)
                    if parent is None:
                        parent = node_parent.get(key)
                    if parent is not None:
                        parents = [Path(parent)]

                if parents:
                    stack.extend(parents)
                elif node in forest_roots:
                    roots.add(node)

        return roots

    def clear(self):
        self._forest = None
        self._forest_ref = None
        self._snapshot_id = ""
        self._forest_snapshot_id = ""
        self._stale = False
        self._snapshot_service.reset()
        self._virtual_paths.clear()
        self._generation += 1

    # -------------------------------------------------- VFS проекта
    def apply_overlay(self, overlay, services=None):
        """Привести виртуальные файлы леса в соответствие с overlay.

        Возвращает ``(added, removed)``. Оригинальные файлы на диске не
        изменяются: только ``forest.files``/``forest.xml_files``.
        """
        forest = self._forest
        services = services if services is not None else self._services
        if forest is None or services is None:
            return 0, 0

        self.update_overlay(getattr(overlay, "revision", ""))

        source_files = getattr(overlay, "source_files", None) or {}
        wanted = {
            Path(path)
            for path in source_files
            if Path(path).suffix.lower() in (".ltx", ".xml")
        }
        new_files = {
            Path(path)
            for path in (getattr(overlay, "new_files", None) or ())
            if Path(path).suffix.lower() in (".ltx", ".xml")
        }
        # Файлы, которые уже попали в лес из TreeBuilder(overlay=...),
        # не добавлялись через этот сервис. Восстанавливаем их учёт,
        # иначе при смене проекта они останутся висеть в дереве.
        for path in wanted & new_files:
            if path in forest.files or path in forest.xml_files:
                self._virtual_paths.add(path)
        self.last_added_paths = set()
        self.last_removed_paths = set(self._virtual_paths - wanted)
        self.last_removed_roots = self.roots_for(self.last_removed_paths)

        removed = 0
        for path in list(self._virtual_paths - wanted):
            try:
                if services.remove_virtual_file(forest, path):
                    removed += 1
            finally:
                self._virtual_paths.discard(path)

        added = 0
        for path in sorted(wanted, key=lambda item: str(item).lower()):
            if path in forest.files or path in forest.xml_files:
                continue
            text = str(source_files.get(path, ""))
            try:
                if services.add_virtual_file(forest, path, text):
                    self._virtual_paths.add(path)
                    self.last_added_paths.add(path)
                    added += 1
            except Exception:
                continue

        overlay_revision = str(getattr(overlay, "revision", "") or "")
        if added or removed:
            snapshot = self._snapshot_service.observe_forest(
                forest,
                overlay_revision=overlay_revision,
            )
            forest.cache_id = snapshot.id
            forest.overlay_revision = snapshot.overlay_revision
            self._forest_snapshot_id = snapshot.id
            self._snapshot_id = snapshot.id
            self._stale = False
            self._generation += 1
        else:
            forest.overlay_revision = overlay_revision
            self._refresh_stale()
        return added, removed
