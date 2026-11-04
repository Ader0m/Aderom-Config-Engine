# -*- coding: utf-8 -*-
"""Диалоги проекта: создание, загрузка и предпросмотр патча."""

from __future__ import annotations

from typing import Callable, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFontDatabase
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .project import ProjectPatchPreview

try:
    from .theme import PALETTE
except Exception:  # pragma: no cover - запуск без UI-темы
    PALETTE = None


class ProjectCreateDialog(QDialog):
    """Создание проекта: имя и две заглушки из требований."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Создать проект")
        self.resize(520, 260)

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Например: Баланс оружия")
        form.addRow("Название:", self.name_edit)

        self.modules_edit = QLineEdit("Не реализовано")
        self.modules_edit.setEnabled(False)
        self.modules_edit.setToolTip(
            "Подключенные модули проекта — заглушка"
        )
        form.addRow("Подключенные модули:", self.modules_edit)

        self.save_mode_combo = QComboBox()
        self.save_mode_combo.addItem(
            "По файлам (one by one)",
            "one_by_one",
        )
        self.save_mode_combo.addItem(
            "Одним патчем на корень (all in one)",
            "all_in_one",
        )
        self.save_mode_combo.setToolTip(
            "one by one — отдельный патч на каждый изменённый файл; "
            "all in one — изменения файлов одного корня собираются "
            "в один патч"
        )
        form.addRow("Режим сохранения:", self.save_mode_combo)

        layout.addLayout(form)

        hint = QLabel(
            "Проект хранится автоматически: Ctrl+S, раз в минуту и при "
            "закрытии приложения. Оригинальные файлы не изменяются."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self):
        if not self.name_edit.text().strip():
            self.name_edit.setFocus()
            return
        self.accept()

    def values(self):
        return (
            self.name_edit.text().strip(),
            [],
            str(self.save_mode_combo.currentData() or "one_by_one"),
        )


class ProjectSettingsDialog(QDialog):
    """Настройки активного проекта."""

    DELETE_CLICKS = 5

    def __init__(self, project, parent=None, on_delete=None):
        super().__init__(parent)
        self.project = project
        self._on_delete = on_delete
        self._delete_clicks = 0
        self.deleted = False

        self.setWindowTitle("Настройки проекта")
        self.resize(560, 280)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit(project.name)
        self.name_edit.setReadOnly(True)
        form.addRow("Название:", self.name_edit)

        self.path_edit = QLineEdit(str(project.root_dir))
        self.path_edit.setReadOnly(True)
        form.addRow("Каталог:", self.path_edit)

        self.modules_edit = QLineEdit(
            ", ".join(project.modules) if project.modules else "Не реализовано"
        )
        self.modules_edit.setEnabled(False)
        self.modules_edit.setToolTip("Подключенные модули — заглушка")
        form.addRow("Подключенные модули:", self.modules_edit)

        self.save_mode_combo = QComboBox()
        self.save_mode_combo.addItem(
            "По файлам (one by one)",
            "one_by_one",
        )
        self.save_mode_combo.addItem(
            "Одним патчем на корень (all in one)",
            "all_in_one",
        )
        index = self.save_mode_combo.findData(
            project.save_mode or "one_by_one"
        )
        if index >= 0:
            self.save_mode_combo.setCurrentIndex(index)
        form.addRow("Режим сохранения:", self.save_mode_combo)

        layout.addLayout(form)

        hint = QLabel(
            "one by one — отдельный патч на каждый изменённый файл. "
            "all in one — изменения файлов одного корня собираются "
            "в один патч."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        layout.addWidget(hint)

        bottom = QHBoxLayout()

        red = (
            PALETTE.accent_red
            if PALETTE is not None
            else "#F14C4C"
        )
        bright = (
            PALETTE.text_bright
            if PALETTE is not None
            else "#FFFFFF"
        )

        self.delete_button = QPushButton("Удалить проект")
        self.delete_button.setMinimumWidth(230)
        self.delete_button.setToolTip(
            "Нажмите 5 раз, чтобы удалить проект со всеми черновиками"
        )
        self.delete_button.setStyleSheet(
            "QPushButton {"
            " background-color: %s;"
            " color: %s;"
            " font-weight: 600;"
            " padding: 6px 12px;"
            " border: none;"
            " border-radius: 4px;"
            "}"
            % (red, bright)
        )
        self.delete_button.clicked.connect(self._on_delete_clicked)
        bottom.addWidget(self.delete_button)
        bottom.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        bottom.addWidget(buttons)

        layout.addLayout(bottom)

    @staticmethod
    def _clicks_word(count: int) -> str:
        return "нажатие" if count == 1 else "нажатия"

    def _on_delete_clicked(self):
        if self._on_delete is None:
            return

        self._delete_clicks += 1
        remaining = self.DELETE_CLICKS - self._delete_clicks

        if remaining <= 0:
            if self._on_delete():
                self.deleted = True
                self.accept()
            else:
                self._delete_clicks = 0
                self.delete_button.setText("Удалить проект")
            return

        self.delete_button.setText(
            "Удалить проект (%d %s)"
            % (remaining, self._clicks_word(remaining))
        )

    def save_mode(self) -> str:
        return str(self.save_mode_combo.currentData() or "one_by_one")


class ProjectLoadDialog(QDialog):
    """Выбор существующего проекта."""

    def __init__(self, projects, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Загрузить проект")
        self.resize(560, 380)

        layout = QVBoxLayout(self)

        self.list = QListWidget()
        for info in projects:
            mode = (
                "all in one"
                if info.save_mode == "all_in_one"
                else "one by one"
            )
            item = QListWidgetItem(
                "%s  —  файлов: %d  —  %s"
                % (info.name, info.draft_count, mode)
            )
            item.setData(Qt.UserRole, info.id)
            item.setToolTip(str(info.root_dir))
            self.list.addItem(item)

        if self.list.count():
            self.list.setCurrentRow(0)

        layout.addWidget(self.list, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_id(self) -> Optional[str]:
        item = self.list.currentItem()
        if item is None:
            return None
        return item.data(Qt.UserRole)


class ProjectPreviewDialog(QDialog):
    """Предпросмотр всех патчей проекта."""

    def __init__(
        self,
        preview: ProjectPatchPreview,
        parent=None,
        on_write: Optional[Callable] = None,
    ):
        super().__init__(parent)
        self.preview = preview
        self._on_write = on_write

        self.setWindowTitle("Предпросмотр патча — %s" % preview.project.name)
        self.resize(1100, 720)

        layout = QVBoxLayout(self)

        mode = (
            "all in one"
            if preview.project.save_mode == "all_in_one"
            else "one by one"
        )
        header = QLabel(
            "Проект: %s. Режим: %s. Файлов к патчу: %d. Успешно: %d."
            % (
                preview.project.name,
                mode,
                len(preview.items),
                len(preview.ok_items),
            )
        )
        layout.addWidget(header)

        splitter = QSplitter(Qt.Horizontal)

        self.list = QListWidget()
        self.list.setMinimumWidth(280)
        for item in preview.items:
            status = "OK" if item.ok else "ОШИБКА"
            entry = QListWidgetItem(
                "%s  [%s]" % (item.title, status)
            )
            entry.setData(Qt.UserRole, item)
            entry.setToolTip(str(item.source_path))
            self.list.addItem(entry)
        self.list.currentRowChanged.connect(self._show_item)
        splitter.addWidget(self.list)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(170)
        right_layout.addWidget(self.details)

        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Plain)
        separator.setFixedHeight(1)
        if PALETTE is not None:
            separator.setStyleSheet(
                "QFrame { background-color: %s; }" % PALETTE.bg_titlebar
            )
        right_layout.addWidget(separator)

        self.patch_text = QPlainTextEdit()
        self.patch_text.setReadOnly(True)
        fixed = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        self.patch_text.setFont(fixed)
        if PALETTE is not None:
            self.patch_text.setStyleSheet(
                "QPlainTextEdit { background-color: %s; color: %s; }"
                % (PALETTE.bg_main, PALETTE.syn_default)
            )
        right_layout.addWidget(self.patch_text, 1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 780])
        layout.addWidget(splitter, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)

        self.write_button = QPushButton("Записать в патч-мод...")
        self.write_button.setEnabled(bool(preview.ok_items) and on_write is not None)
        self.write_button.clicked.connect(self._write)
        buttons.addWidget(self.write_button)

        close_button = QPushButton("Закрыть")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)

        layout.addLayout(buttons)

        if self.list.count():
            self.list.setCurrentRow(0)

    def _show_item(self, row):
        if row < 0:
            return

        item = self.list.item(row).data(Qt.UserRole)
        if item is None:
            return

        lines = [
            "Файл: %s" % item.source_path,
            "Режим: %s" % (
                {
                    "resolved": "resolved",
                    "root": "all in one",
                }.get(item.kind, "исходный файл")
            ),
            "Изменений: %d" % (
                len(item.plan.changes) if item.plan is not None else 0
            ),
        ]

        if item.notes:
            lines.append("Изменённые строки и победители:")
            for note in item.notes:
                lines.append("  " + note)

        if item.plan is not None:
            target = item.plan.target.root_path
            lines.append("Корень: %s" % (target or "не определён"))
            lines.append(
                "Патч-файл: %s" % (item.plan.patch_file_rel or "не определён")
            )

            for issue in item.plan.errors():
                lines.append("Ошибка: %s" % issue.message)
            for issue in item.plan.warnings:
                lines.append("Предупреждение: %s" % issue.message)

        if item.generated is not None and item.generated.verification is not None:
            verification = item.generated.verification
            lines.append(
                "Проверка: %s" % ("успешно" if verification.ok else "ошибка")
            )
            for check in verification.failed_checks:
                lines.append("  %s: %s" % (check.name, check.detail))
            for issue in verification.issues:
                if issue.severity == "error":
                    lines.append("  %s" % issue.message)

        if item.error:
            lines.append("Ошибка подготовки: %s" % item.error)

        self.details.setPlainText("\n".join(lines))

        if item.generated is not None:
            self.patch_text.setPlainText(item.generated.text)
        elif item.plan is not None:
            self.patch_text.setPlainText(item.plan.preview)
        else:
            self.patch_text.setPlainText(item.error)

    def _write(self):
        if self._on_write is None:
            return
        if self._on_write(self.preview):
            self.accept()
