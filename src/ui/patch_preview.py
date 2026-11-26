# -*- coding: utf-8 -*-
"""Окно предпросмотра патча.

Окно живёт столько же, сколько платформа: закрытие пользователем только
скрывает его, а повторное открытие не пересобирает данные. Слева показана
VFS-проекция только тех файлов, которые попали в патч; справа — их
итоговый текст в read-only QScintilla; снизу — отдельный лог патча.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QBrush, QColor, QKeySequence
from PyQt5.QtWidgets import (
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QShortcut,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.patching.models import PreviewItem, ProjectPatchPreview
from app.projects.line_changes import (
    expand_change_ranges,
    line_numbers_to_ranges,
)

from .diagnostics import DiagnosticsLog
from .editor import CodeEditor
from .file_tree import FileTreeWidget
from .platform.theme import theme_service


def _display_patch_path(path):
    """Путь внутри патч-мода без служебного корня ``gamedata``."""
    parts = Path(path).parts
    if parts and parts[0].lower() == "gamedata":
        parts = parts[1:]
    return Path(*parts) if parts else Path(path)


def _actual_patch_path(path):
    return Path("gamedata") / Path(path)


@dataclass
class _PatchFileEntry:
    """Один итоговый файл патч-мода и элементы, которые его создают."""

    path: Path
    item_indices: list = field(default_factory=list)
    files: list = field(default_factory=list)


class _PatchPreviewForest:
    """Минимальная VFS-проекция для :class:`FileTreeWidget`.

    Содержит только итоговые файлы патча. Формат повторяет поля LtxForest,
    которые нужны виджету дерева, поэтому внешний вид и навигация остаются
    такими же, как у основного VFS-дерева.
    """

    def __init__(self, paths):
        self.scan_dir = Path("Патч-мод")
        self.system_root = None
        self.total_files = 0
        self.total_xml_files = 0
        self.rel_paths = {}
        self.display_children = {}
        self.subtree_counts = {}
        self.node_parent = {}
        files = set()

        for raw_path in paths:
            path = Path(raw_path)
            parts = path.parts
            if not parts:
                continue
            files.add(path)
            self.rel_paths[path] = "/".join(parts)
            self.subtree_counts[path] = 1
            # Виртуальные папки FileTreeWidget строит сам по сегментам
            # относительного пути. Реальные узлы здесь — только файлы.
            self.node_parent[path] = None

        self.roots = sorted(files, key=lambda item: str(item).lower())
        self.total_files = len(files)


class ProjectPreviewDialog(QMainWindow):
    """Предпросмотр всех патчей проекта."""

    def __init__(
        self,
        preview: ProjectPatchPreview,
        parent=None,
        on_write: Optional[Callable] = None,
        on_rollback: Optional[Callable] = None,
        on_cancel: Optional[Callable] = None,
        # Fail-safe: запись разрешается только явным write_enabled=True.
        write_enabled: bool = False,
        log_lines=None,
    ):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.preview = preview
        self._on_write = on_write
        self._on_rollback = on_rollback
        self._on_cancel = on_cancel
        self._write_enabled = bool(write_enabled)
        self._writing = False
        self._cancel_requested = False
        self._allow_close = False

        self._items = list(preview.items)
        self._path_entries = {}
        self._selected_path = None
        self._planner_log_lines = []
        self._file_log_lines = []

        self.setWindowTitle(
            "Предпросмотр патча — %s" % preview.project.name
        )
        self.resize(1400, 900)

        central = QWidget()
        central.setObjectName("patchPreviewCentral")
        central.setStyleSheet(
            "#patchPreviewCentral { background-color: %s; }"
            % theme_service().palette.bg_main
        )
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        self._header = QLabel()
        layout.addWidget(self._header)
        if not self._write_enabled:
            status = QLabel(
                "Запись патчей отключена: доступен только предпросмотр."
            )
            status.setWordWrap(True)
            layout.addWidget(status)

        self._top_splitter = QSplitter(Qt.Horizontal)

        self.tree = FileTreeWidget()
        self.tree.setMinimumWidth(320)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(
            self._tree_context_menu
        )
        self.tree.currentItemChanged.connect(self._on_tree_current_changed)
        self._top_splitter.addWidget(self.tree)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)

        self.file_info = QLabel("Выберите файл патча")
        self.file_info.setWordWrap(True)
        self.file_info.setProperty("role", "muted")
        right_layout.addWidget(self.file_info)

        self.code = CodeEditor()
        self.code.setMinimumHeight(320)
        right_layout.addWidget(self.code, 1)

        self._top_splitter.addWidget(right)
        self._top_splitter.setStretchFactor(0, 0)
        self._top_splitter.setStretchFactor(1, 1)
        self._top_splitter.setSizes([380, 1020])

        layout.addWidget(self._top_splitter, 1)
        self.setCentralWidget(central)

        self.log_view = DiagnosticsLog()
        self.log_dock = QDockWidget("Диагностика", self)
        self.log_dock.setObjectName("patchPreviewLogDock")
        self.log_dock.setWidget(self.log_view)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log_dock)
        self.resizeDocks([self.log_dock], [170], Qt.Vertical)

        status_bar = self.statusBar()
        status_bar.showMessage("Предпросмотр патча")

        self.write_button = QPushButton("Записать в патч-мод...")
        self.write_button.setAutoDefault(True)
        self.write_button.setDefault(True)
        self.write_button.clicked.connect(self._write)
        if not self._write_enabled:
            self.write_button.setToolTip(
                "Запись патчей отключена: доступен только предпросмотр."
            )
        status_bar.addPermanentWidget(self.write_button)

        self.cancel_button = QPushButton("Отменить запись")
        self.cancel_button.clicked.connect(self._cancel_write)
        self.cancel_button.setVisible(False)
        status_bar.addPermanentWidget(self.cancel_button)

        self.close_button = QPushButton("Закрыть")
        self.close_button.clicked.connect(self.close)
        status_bar.addPermanentWidget(self.close_button)

        QShortcut(
            QKeySequence("Esc"),
            self,
            activated=self.reject,
        )

        self.set_preview(preview, log_lines=log_lines)

    # -------------------------------------------------- данные
    def set_preview(self, preview, log_lines=None):
        """Заменить содержимое окна, сохранив сам экземпляр окна."""
        self.preview = preview
        self._items = list(preview.items)
        self.setWindowTitle(
            "Предпросмотр патча — %s" % preview.project.name
        )
        self._mode_text, self._xml_mode_text = self._mode_labels(preview)
        if log_lines is not None:
            self._planner_log_lines = [str(line) for line in log_lines]
        self._append_item_errors()
        self._rebuild_view()
        self._render_log()
        self._refresh_write_button()

    def _append_item_errors(self):
        for item in self._items:
            if item.error:
                self._planner_log_lines.append(
                    "Патч: %s — %s" % (item.title, item.error)
                )
                continue
            generated = getattr(item, "generated", None)
            verification = getattr(generated, "verification", None)
            if verification is not None and not verification.ok:
                self._planner_log_lines.append(
                    "Патч: %s — проверка не пройдена" % item.title
                )

    @staticmethod
    def _mode_labels(preview):
        mode = {
            "root": "Root",
            "on_file": "On-file",
            "override": "Override",
        }.get(preview.project.save_mode, preview.project.save_mode)
        xml_mode = {
            "dxml_lua": "DXML-Lua",
            "xml": "XML",
            "xml_override": "Override",
        }.get(
            preview.project.xml_patch_mode,
            preview.project.xml_patch_mode,
        )
        return mode, xml_mode

    def _collect_file_entries(self):
        entries = {}

        def add(path, item_index, patch_file=None):
            display = _display_patch_path(path)
            key = str(display)
            entry = entries.get(key)
            if entry is None:
                entry = _PatchFileEntry(path=display)
                entries[key] = entry
            if item_index not in entry.item_indices:
                entry.item_indices.append(item_index)
            if patch_file is not None and patch_file not in entry.files:
                entry.files.append(patch_file)

        for index, item in enumerate(self._items):
            files = tuple(
                getattr(getattr(item, "generated", None), "files", ()) or ()
            )
            if files:
                for patch_file in files:
                    add(
                        getattr(patch_file, "relative_path", ""),
                        index,
                        patch_file,
                    )
                continue

            planned = self._planned_patch_path(item)
            if planned is not None:
                add(planned, index)

        return entries

    @staticmethod
    def _planned_patch_path(item):
        plan = getattr(item, "plan", None)
        if plan is None:
            return None
        rel = getattr(plan, "patch_file_rel", None)
        if rel is None:
            return None
        target_dir = getattr(plan, "patch_target_dir", "") or "configs"
        return Path("gamedata") / target_dir / rel

    def _rebuild_view(self, keep_path=None):
        if keep_path is None:
            keep_path = self._selected_path
            current = self._item_path(self.tree.currentItem())
            if current is not None:
                keep_path = current

        self._path_entries = self._collect_file_entries()
        paths = tuple(
            entry.path for entry in self._path_entries.values()
        )
        self.tree.load_forest(_PatchPreviewForest(paths))

        self._update_header()

        if not self._path_entries:
            self._selected_path = None
            self._file_log_lines = []
            self.file_info.setText("Патч не создаёт файлов")
            self.code.show_text(
                "В предпросмотре нет файлов, которые попадут в патч-мод.",
                "Патч",
                readonly=True,
                syntax=False,
            )
            self._render_log()
            return

        target = keep_path
        if target is None or str(target) not in self._path_entries:
            target = next(iter(self._path_entries.values())).path
        self.tree.reveal_path(Path(target))
        self.tree.expandAll()
        self._apply_tree_styles()
        item = self.tree.currentItem()
        path = self._item_path(item)
        if path is None:
            path = Path(target)
            self.tree.reveal_path(path)
        self._show_patch_path(Path(path))

    def _apply_tree_styles(self):
        disabled = QBrush(
            QColor(theme_service().palette.text_disabled)
        )
        error = QBrush(QColor(theme_service().palette.accent_red))
        normal = QBrush()

        for entry in self._path_entries.values():
            item = self.tree.item_for_path(entry.path)
            if item is None:
                continue
            items = self._entries_for_item(entry)
            details = []
            for preview_item in items:
                details.extend(self._item_detail_lines(preview_item))
            item.setToolTip(0, "\n".join(details))
            if items and all(preview_item.excluded for preview_item in items):
                item.setForeground(0, disabled)
            elif any(not preview_item.ok for preview_item in items):
                item.setForeground(0, error)
            else:
                item.setForeground(0, normal)

    def patch_paths(self):
        return tuple(entry.path for entry in self._path_entries.values())

    def select_patch_path(self, path):
        path = Path(path)
        if str(path) not in self._path_entries:
            return False
        self.tree.reveal_path(path)
        self._show_patch_path(path)
        return True

    @staticmethod
    def _item_path(item):
        if item is None:
            return None
        key = item.data(0, Qt.UserRole)
        if not key or str(key).startswith("::"):
            return None
        return Path(key)

    def _entries_for_item(self, entry):
        return [
            self._items[index]
            for index in entry.item_indices
            if 0 <= index < len(self._items)
        ]

    # -------------------------------------------------- отображение
    def _on_tree_current_changed(self, current, _previous):
        path = self._item_path(current)
        if path is None:
            return
        self._show_patch_path(path)

    def _show_patch_path(self, path):
        entry = self._path_entries.get(str(path))
        if entry is None:
            return
        self._selected_path = entry.path
        items = self._entries_for_item(entry)
        if not items:
            return

        primary = items[0]
        status = self._entry_status(items)
        details = []
        for item in items:
            details.extend(self._item_detail_lines(item))
        tooltip = "\n".join(details)
        self.file_info.setText(
            "Файл: %s   •   Статус: %s"
            % (_actual_patch_path(entry.path), status)
        )
        self.file_info.setToolTip(tooltip)
        self.statusBar().showMessage(
            "Файл: %s   •   Статус: %s"
            % (_actual_patch_path(entry.path), status)
        )

        self._file_log_lines = [
            "",
            "Лог файла: %s" % _actual_patch_path(entry.path),
            "Статус: %s" % status,
        ] + details
        self._render_log()

        text, label = self._entry_text(entry, primary)
        self.code.show_text(
            text,
            label,
            readonly=True,
            source_path=entry.path,
            change_ranges=self._entry_change_ranges(entry, text),
        )
        self.code.setToolTip(tooltip)

    @staticmethod
    def _same_text(left, right):
        left = str(left or "").replace("\r\n", "\n").replace("\r", "\n")
        right = str(right or "").replace("\r\n", "\n").replace("\r", "\n")
        return left == right or left.rstrip("\n") == right.rstrip("\n")

    @staticmethod
    def _source_matches(source_path, output_path):
        source = str(Path(source_path)).replace("\\", "/").lower()
        output = str(Path("gamedata") / Path(output_path)).replace(
            "\\",
            "/",
        ).lower()
        return (
            source == output
            or source.endswith("/" + output)
            or output.endswith("/" + source)
        )

    @staticmethod
    def _mapped_source_ranges(entry, items):
        """Строки файла-победителя, затронутые правками resolved-текста."""
        mapped = set()
        for item in items:
            resolved = item.resolved
            if resolved is None or resolved.change_ranges is None:
                continue
            sources = tuple(resolved.line_sources or ())
            changed = expand_change_ranges(
                resolved.change_ranges,
                len(sources),
            )
            for index in changed:
                if index < 0 or index >= len(sources):
                    continue
                source = sources[index]
                if source is None:
                    continue
                path = getattr(source, "path", None)
                line = int(getattr(source, "line", 0) or 0)
                if path is None or line <= 0:
                    continue
                if not ProjectPreviewDialog._source_matches(
                    path,
                    entry.path,
                ):
                    continue
                mapped.add(line - 1)
        return line_numbers_to_ranges(mapped) if mapped else None

    def _entry_change_ranges(self, entry, text):
        """Диапазоны изменений, если показан полный текст черновика."""
        items = self._entries_for_item(entry)
        for item in items:
            for source in (item.resolved, item.draft):
                if source is None:
                    continue
                if not self._same_text(source.current_text, text):
                    continue
                return source.change_ranges
        return self._mapped_source_ranges(entry, items)

    def _entry_status(self, items):
        if all(item.excluded for item in items):
            return "исключён"
        if any(not item.ok for item in items):
            return "ОШИБКА"
        return "OK"

    @staticmethod
    def _entry_text(entry, primary):
        label = "Патч: %s" % _actual_patch_path(entry.path)
        for patch_file in entry.files:
            text = str(getattr(patch_file, "text", "") or "")
            if text:
                return text, label
            data = getattr(patch_file, "data", None)
            if data is not None:
                return (
                    "Двоичный файл: %d байт" % len(data),
                    label,
                )

        generated = getattr(primary, "generated", None)
        if generated is not None and generated.text:
            return str(generated.text), label
        if primary.text:
            return str(primary.text), label
        if primary.plan is not None and primary.plan.preview:
            return str(primary.plan.preview), label
        return str(primary.error or "Нет данных для отображения"), label

    def _item_detail_lines(self, item):
        lines = [
            "Файл: %s" % item.source_path,
            "Режим: %s"
            % {
                "resolved": "resolved",
                "root": "Root",
                "script": "скрипт",
                "info": "информация",
                "new_file": "новый файл",
                "asset": "двоичный ресурс",
            }.get(item.kind, "исходный файл"),
            "Изменений: %d"
            % (
                len(item.plan.changes) if item.plan is not None else 0
            ),
        ]
        if item.excluded:
            lines.append(
                "Файл исключён из патча: в патч-мод не попадёт. "
                "Правки проекта и сам файл не затрагиваются."
            )
        if item.notes:
            lines.append("Изменённые строки и победители:")
            lines.extend("  " + note for note in item.notes)
        if item.plan is not None:
            target = item.plan.target.root_path
            lines.append("Корень: %s" % (target or "не определён"))
            patch_rel = item.plan.patch_file_rel
            lines.append(
                "Патч-файл: %s"
                % (
                    Path("gamedata")
                    / (item.plan.patch_target_dir or "configs")
                    / (patch_rel or "не определён")
                    if patch_rel is not None
                    else "не определён"
                )
            )
            for issue in item.plan.errors():
                lines.append("Ошибка: %s" % issue.message)
            for issue in item.plan.warnings:
                lines.append("Предупреждение: %s" % issue.message)
        generated = getattr(item, "generated", None)
        verification = getattr(generated, "verification", None)
        if verification is not None:
            lines.append(
                "Проверка: %s"
                % ("успешно" if verification.ok else "ошибка")
            )
            important = {
                "full_diff",
                "source",
                "dltx_artifacts",
                "parents",
                "roundtrip",
            }
            for check in verification.checks:
                if check.ok and check.name not in important:
                    continue
                lines.append(
                    "  %s: %s"
                    % ("OK" if check.ok else "ОШИБКА", check.name)
                )
                if check.detail:
                    lines.append("    " + str(check.detail))
            for issue in verification.issues:
                if issue.severity == "error":
                    lines.append("  %s" % issue.message)
        if item.roundtrip is not None:
            report = item.roundtrip
            lines.append(
                "Полная проверка root: %s"
                % ("успешно" if report.ok else "ошибка")
            )
            for check in report.checks:
                if check.ok and check.name not in {
                    "full_diff",
                    "source",
                    "dltx_artifacts",
                    "parents",
                    "roundtrip",
                }:
                    continue
                lines.append(
                    "  %s: %s"
                    % ("OK" if check.ok else "ОШИБКА", check.name)
                )
                if check.detail:
                    lines.append("    " + str(check.detail))
            for issue in report.issues:
                if issue.severity == "error":
                    lines.append("  %s" % issue.message)
        if item.error:
            lines.append("Ошибка подготовки: %s" % item.error)
        return lines

    # -------------------------------------------------- лог
    def append_log(self, message):
        text = str(message)
        self._planner_log_lines.append(text)
        self.log_view.append_message(text)

    def set_log_lines(self, lines):
        self._planner_log_lines = [str(line) for line in lines or ()]
        self._file_log_lines = []
        self._render_log()

    def _render_log(self):
        """Показать общий лог патча, затем лог выбранного файла."""
        self.log_view.set_lines(
            list(self._planner_log_lines) + list(self._file_log_lines)
        )

    # -------------------------------------------------- заголовок/запись
    def _selector_items(self):
        return tuple(self._items)

    def _update_header(self):
        items = self._selector_items()
        included = [item for item in items if not item.excluded]
        ok_included = [
            item for item in included if item.is_patch and item.ok
        ]
        excluded = sum(1 for item in items if item.excluded)
        text = (
            "Проект: %s. DLTX: %s. DXML: %s. Файлов к патчу: %d. "
            "Успешно: %d."
            % (
                self.preview.project.name,
                self._mode_text,
                self._xml_mode_text,
                len(included),
                len(ok_included),
            )
        )
        if excluded:
            text += " Исключено: %d." % excluded
        self._header.setText(text)

    def _has_verified_items(self):
        return any(
            item.is_patch and item.ok and not item.excluded
            for item in self._selector_items()
        )

    def _refresh_write_button(self):
        has_ok = self._has_verified_items()
        self.write_button.setEnabled(
            self._write_enabled
            and has_ok
            and self._on_write is not None
            and not self._writing
        )
        if self._write_enabled and not has_ok:
            self.write_button.setToolTip(
                "Нет проверенных файлов для записи."
            )

    # -------------------------------------------------- контекст файла
    def _tree_context_menu(self, pos):
        entry_item = self.tree.itemAt(pos)
        path = self._item_path(entry_item)
        if path is None:
            return
        entry = self._path_entries.get(str(path))
        if entry is None:
            return
        items = self._entries_for_item(entry)
        if not items:
            return
        rollback_item = next(
            (
                item
                for item in items
                if item.can_rollback and self._on_rollback is not None
            ),
            None,
        )
        can_toggle = all(item.is_patch for item in items)
        if rollback_item is None and not can_toggle:
            return

        self.tree.setCurrentItem(entry_item)

        menu = QMenu(self.tree)
        if rollback_item is not None:
            rollback = menu.addAction("Откатить изменения")
            rollback.triggered.connect(
                lambda _checked=False, item=entry_item: (
                    self._rollback_entry(item)
                )
            )
            if can_toggle:
                menu.addSeparator()
        if can_toggle:
            if all(item.excluded for item in items):
                action = menu.addAction("Включить файл в патч")
            else:
                action = menu.addAction("Не включать файл в патч")
            action.triggered.connect(
                lambda _checked=False, item=entry_item: (
                    self._toggle_excluded(item)
                )
            )
        menu.exec_(self.tree.viewport().mapToGlobal(pos))

    def _toggle_excluded(self, entry_item):
        path = self._item_path(entry_item)
        entry = self._path_entries.get(str(path)) if path else None
        if entry is None:
            return
        items = self._entries_for_item(entry)
        if not items or not all(item.is_patch for item in items):
            return

        excluded = not all(item.excluded for item in items)
        for index in list(entry.item_indices):
            if 0 <= index < len(self._items):
                item = self._items[index]
                if item.is_patch:
                    self._items[index] = replace(
                        item,
                        excluded=excluded,
                    )
        self.preview = replace(self.preview, items=tuple(self._items))
        self._rebuild_view(keep_path=entry.path)
        self._refresh_write_button()

    def _rollback_targets(self, item):
        paths = {
            str(Path(path)).lower()
            for path in getattr(item, "rollback_paths", ())
        }
        resolved = {
            str(key)
            for key in getattr(item, "rollback_resolved_ids", ())
        }
        return paths, resolved

    def _rollback_name(self, item) -> str:
        if item.kind == "root":
            return item.title
        name = Path(item.source_path).name
        return name or item.title

    def _rollback_entry(self, entry_item):
        path = self._item_path(entry_item)
        entry = self._path_entries.get(str(path)) if path else None
        if entry is None:
            return
        items = self._entries_for_item(entry)
        item = next(
            (
                candidate
                for candidate in items
                if candidate.can_rollback
            ),
            None,
        )
        if item is None or self._on_rollback is None:
            return

        answer = QMessageBox.question(
            self,
            "Откат изменений",
            "Вы уверены, что хотите потерять все изменения в файле %s?"
            % self._rollback_name(item),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        try:
            ok = bool(self._on_rollback(item))
        except Exception:
            ok = False
        if not ok:
            return

        removed_paths, removed_resolved = self._rollback_targets(item)
        remaining = []
        for candidate in self._items:
            paths, resolved = self._rollback_targets(candidate)
            if paths & removed_paths or resolved & removed_resolved:
                continue
            remaining.append(candidate)

        self._items = remaining
        self.preview = replace(self.preview, items=tuple(remaining))
        self._rebuild_view()
        self._refresh_write_button()

    # -------------------------------------------------- запись
    def _write(self):
        if (
            not self._write_enabled
            or self._on_write is None
            or self._writing
        ):
            return
        if not self._has_verified_items():
            return
        updated = replace(
            self.preview,
            items=self._selector_items(),
        )
        if self._on_write(updated):
            self._writing = True
            self._cancel_requested = False
            self.write_button.setEnabled(False)
            self.close_button.setEnabled(False)
            self.cancel_button.setVisible(True)
            self.cancel_button.setEnabled(True)
            self.setWindowTitle(
                "Запись патча — %s" % self.preview.project.name
            )

    def _cancel_write(self):
        if not self._writing or self._cancel_requested:
            return
        self._cancel_requested = True
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("Отмена запрошена…")
        if self._on_cancel is not None:
            self._on_cancel()

    def finish_write(self, result=None, error=None):
        """Завершить фоновую запись и вернуть диалог в обычное состояние."""
        self._writing = False
        self.cancel_button.setVisible(False)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setText("Отменить запись")
        self.close_button.setEnabled(True)
        self._refresh_write_button()
        if error is not None:
            self.append_log("Патч: запись не выполнена: %s" % error)
            return
        if result is not None:
            self.append_log("Патч: запись завершена: %s" % result.mod_dir)
        self.accept()

    # -------------------------------------------------- жизненный цикл
    def force_close(self):
        """Закрыть окно по-настоящему (завершение приложения)."""
        self._allow_close = True
        self.close()
        self.deleteLater()

    def closeEvent(self, event):
        if self._allow_close:
            super().closeEvent(event)
            return
        event.ignore()
        if not self._writing:
            self.hide()

    def accept(self):
        self.hide()

    def reject(self):
        if self._writing:
            return
        self.hide()

    def refresh_theme(self):
        self.code.refresh_theme()
        self.log_view.refresh_theme()
        self.tree.refresh_theme()
        self.update()
