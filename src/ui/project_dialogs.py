# -*- coding: utf-8 -*-
"""Диалоги проекта: создание, загрузка и предпросмотр патча."""

from __future__ import annotations

from pathlib import Path
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
        self.resize(520, 330)

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
            "Root — один DLTX-патч на корень",
            "root",
        )
        self.save_mode_combo.addItem(
            "In-place — DLTX для файла-победителя",
            "in_place",
        )
        self.save_mode_combo.addItem(
            "Override — полная копия LTX-победителя",
            "override",
        )
        self.save_mode_combo.setToolTip(
            "Root — изменения одного корня собираются в один DLTX-файл; "
            "In-place — патч для файла-победителя строки; "
            "Override — полная копия LTX-файла-победителя"
        )
        form.addRow("DLTX-патчи:", self.save_mode_combo)

        self.xml_mode_combo = QComboBox()
        self.xml_mode_combo.addItem(
            "DXML-Lua — правка XML на месте",
            "dxml_lua",
        )
        self.xml_mode_combo.addItem(
            "XML — подмена ключей",
            "xml",
        )
        self.xml_mode_combo.addItem(
            "Override — полная копия XML-файла",
            "xml_override",
        )
        self.xml_mode_combo.setCurrentIndex(1)
        self.xml_mode_combo.setToolTip(
            "DXML-Lua — изменить XML на месте скриптом; "
            "XML — создать новый XML с новым ключом и перепривязать "
            "ссылки; Override — полная копия XML-файла"
        )
        form.addRow("DXML-патчи:", self.xml_mode_combo)

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
            str(self.save_mode_combo.currentData() or "root"),
            str(self.xml_mode_combo.currentData() or "xml"),
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
        self.resize(560, 350)

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
            "Root — один DLTX-патч на корень",
            "root",
        )
        self.save_mode_combo.addItem(
            "In-place — DLTX для файла-победителя",
            "in_place",
        )
        self.save_mode_combo.addItem(
            "Override — полная копия LTX-победителя",
            "override",
        )
        index = self.save_mode_combo.findData(project.save_mode)
        if index < 0:
            index = self.save_mode_combo.findData("root")
        if index >= 0:
            self.save_mode_combo.setCurrentIndex(index)
        form.addRow("DLTX-патчи:", self.save_mode_combo)

        self.xml_mode_combo = QComboBox()
        self.xml_mode_combo.addItem(
            "DXML-Lua — правка XML на месте",
            "dxml_lua",
        )
        self.xml_mode_combo.addItem(
            "XML — подмена ключей",
            "xml",
        )
        self.xml_mode_combo.addItem(
            "Override — полная копия XML-файла",
            "xml_override",
        )
        xml_index = self.xml_mode_combo.findData(project.xml_patch_mode)
        if xml_index < 0:
            xml_index = self.xml_mode_combo.findData("xml")
        if xml_index >= 0:
            self.xml_mode_combo.setCurrentIndex(xml_index)
        form.addRow("DXML-патчи:", self.xml_mode_combo)

        layout.addLayout(form)

        hint = QLabel(
            "Root — один DLTX-патч на корень загрузки. "
            "In-place — отдельный DLTX-патч для файла-победителя "
            "строки. "
            "Override — полная копия LTX-файла-победителя. "
            "DXML-Lua — правка XML скриптом; XML — новый файл с новым "
            "ключом и перепривязкой ссылок; Override — полная копия XML."
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
        return str(self.save_mode_combo.currentData() or "root")

    def xml_patch_mode(self) -> str:
        return str(self.xml_mode_combo.currentData() or "xml")


class ProjectLoadDialog(QDialog):
    """Выбор существующего проекта."""

    def __init__(self, projects, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Загрузить проект")
        self.resize(560, 380)

        layout = QVBoxLayout(self)

        self.list = QListWidget()
        for info in projects:
            mode = {
                "root": "Root",
                "in_place": "In-place",
                "override": "Override",
            }.get(info.save_mode, info.save_mode)
            xml_mode = {
                "dxml_lua": "DXML-Lua",
                "xml": "XML",
                "xml_override": "Override",
            }.get(info.xml_patch_mode, info.xml_patch_mode)
            item = QListWidgetItem(
                "%s  —  файлов: %d  —  DLTX: %s, DXML: %s"
                % (info.name, info.draft_count, mode, xml_mode)
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

        mode = {
            "root": "Root",
            "in_place": "In-place",
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
        header = QLabel(
            "Проект: %s. DLTX: %s. DXML: %s. Файлов к патчу: %d. "
            "Успешно: %d."
            % (
                preview.project.name,
                mode,
                xml_mode,
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
                    "root": "Root",
                    "script": "скрипт",
                    "info": "информация",
                    "new_file": "новый файл",
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
                "Патч-файл: %s"
                % (
                    Path("gamedata")
                    / (item.plan.patch_target_dir or "configs")
                    / (item.plan.patch_file_rel or "не определён")
                    if item.plan.patch_file_rel is not None
                    else "не определён"
                )
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

        if item.text:
            self.patch_text.setPlainText(item.text)
        elif item.generated is not None:
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
