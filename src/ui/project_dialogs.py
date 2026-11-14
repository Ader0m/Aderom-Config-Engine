# -*- coding: utf-8 -*-
"""Диалоги проекта: создание, загрузка и предпросмотр патча."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFontDatabase
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.projects import ProjectPatchPreview

try:
    from .theme import PALETTE
except Exception:  # pragma: no cover - запуск без UI-темы
    PALETTE = None


_DLTX_HELP = (
    "Root — один DLTX-патч на корень загрузки. "
    "On-file — отдельный DLTX-патч на каждый изменённый файл, "
    "но с привязкой к корню загрузки. "
    "Override — полная копия LTX-файла-победителя."
)

_DXML_HELP = (
    "DXML-Lua — правка XML на месте скриптом. "
    "XML — новый файл с новым ключом и перепривязкой ссылок. "
    "Override — полная копия XML-файла."
)


class ProjectSettingsDialog(QDialog):
    """Единое окно создания проекта и его настроек."""

    DELETE_CLICKS = 5

    def __init__(
        self,
        project=None,
        parent=None,
        on_delete=None,
        settings=(),
        setting_values=None,
        base_dir=None,
        suggest_dir=None,
    ):
        super().__init__(parent)
        self.project = project
        self.is_create = project is None
        self._on_delete = on_delete
        self._delete_clicks = 0
        self.deleted = False
        self._setting_widgets = {}
        self._base_dir = Path(base_dir) if base_dir else None
        self._suggest_dir = suggest_dir
        self._dir_manual = False
        settings = tuple(settings or ())

        self.setWindowTitle(
            "Создать проект" if self.is_create else "Настройки проекта"
        )
        self.resize(600, 380 + 44 * len(settings))

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit(
            "" if self.is_create else project.name
        )
        self.name_edit.setReadOnly(not self.is_create)
        self.name_edit.setPlaceholderText("Например: Баланс оружия")
        self.name_edit.textChanged.connect(
            self._refresh_suggested_directory
        )
        form.addRow(
            "Название:",
            self._with_help(
                self.name_edit,
                "Название проекта. Используется в имени патч-мода "
                "и в названии каталога проекта.",
            ),
        )

        self.path_edit = QLineEdit(
            self._suggested_directory()
            if self.is_create
            else str(project.root_dir)
        )
        self.path_edit.setReadOnly(not self.is_create)
        if self.is_create:
            self.path_edit.textEdited.connect(
                self._on_directory_edited
            )
            path_field = QWidget()
            path_row = QHBoxLayout(path_field)
            path_row.setContentsMargins(0, 0, 0, 0)
            path_row.setSpacing(4)
            path_row.addWidget(self.path_edit, 1)
            self.browse_button = QToolButton()
            self.browse_button.setText("…")
            self.browse_button.setToolTip("Выбрать каталог проекта")
            self.browse_button.clicked.connect(self._choose_directory)
            path_row.addWidget(self.browse_button, 0)
        else:
            path_field = self.path_edit
        form.addRow(
            "Каталог:",
            self._with_help(
                path_field,
                (
                    "Каталог, в котором будет создан проект. "
                    "Можно выбрать существующую пустую папку."
                    if self.is_create
                    else "Каталог проекта на диске."
                ),
            ),
        )

        self.modules_edit = QLineEdit(
            ", ".join(project.modules)
            if project is not None and project.modules
            else "Не реализовано"
        )
        self.modules_edit.setEnabled(False)
        form.addRow(
            "Подключенные модули:",
            self._with_help(
                self.modules_edit,
                "Подключенные модули проекта — заглушка.",
            ),
        )

        self.save_mode_combo = QComboBox()
        self.save_mode_combo.addItem(
            "Root — один DLTX-патч на корень",
            "root",
        )
        self.save_mode_combo.addItem(
            "On-file — отдельный DLTX-патч на каждый файл",
            "on_file",
        )
        self.save_mode_combo.addItem(
            "Override — полная копия LTX-победителя",
            "override",
        )
        index = self.save_mode_combo.findData(
            project.save_mode if project is not None else "root"
        )
        if index < 0:
            index = self.save_mode_combo.findData("root")
        if index >= 0:
            self.save_mode_combo.setCurrentIndex(index)
        form.addRow(
            "DLTX-патчи:",
            self._with_help(self.save_mode_combo, _DLTX_HELP),
        )

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
        xml_index = self.xml_mode_combo.findData(
            project.xml_patch_mode if project is not None else "xml"
        )
        if xml_index < 0:
            xml_index = self.xml_mode_combo.findData("xml")
        if xml_index >= 0:
            self.xml_mode_combo.setCurrentIndex(xml_index)
        form.addRow(
            "DXML-патчи:",
            self._with_help(self.xml_mode_combo, _DXML_HELP),
        )

        layout.addLayout(form)

        if settings:
            group = QGroupBox("Настройки модулей")
            group_form = QFormLayout(group)
            values = dict(setting_values or {})

            for setting in settings:
                key = str(getattr(setting, "key", "") or "")
                if not key:
                    continue
                kind = str(
                    getattr(setting, "kind", "") or "choice"
                ).strip().lower()
                default = getattr(setting, "default", None)
                current = values.get(key, default)

                if kind == "choice":
                    widget = QComboBox()
                    for choice in getattr(setting, "choices", ()) or ():
                        value = str(getattr(choice, "value", choice) or "")
                        label = str(
                            getattr(choice, "label", "") or value
                        )
                        widget.addItem(label, value)
                    index = widget.findData(str(current))
                    if index >= 0:
                        widget.setCurrentIndex(index)
                elif kind == "bool":
                    widget = QCheckBox()
                    widget.setChecked(bool(current))
                else:
                    widget = QLineEdit(
                        "" if current is None else str(current)
                    )

                description = str(
                    getattr(setting, "description", "") or ""
                )
                label = str(getattr(setting, "label", "") or key)
                group_form.addRow(
                    label,
                    self._with_help(widget, description),
                )
                self._setting_widgets[key] = (setting, widget)

            layout.addWidget(group)

        bottom = QHBoxLayout()
        self.delete_button = None

        if not self.is_create:
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
        buttons.button(QDialogButtonBox.Ok).setText(
            "Создать" if self.is_create else "Сохранить"
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        bottom.addWidget(buttons)

        layout.addLayout(bottom)

    @staticmethod
    def _with_help(widget, description):
        """Обернуть поле кнопкой «?» с подсказкой вместо длинного текста."""
        if not description:
            return widget

        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        row.addWidget(widget, 1)

        help_button = QToolButton()
        help_button.setText("?")
        help_button.setAutoRaise(True)
        help_button.setCursor(Qt.WhatsThisCursor)
        help_button.setToolTip(str(description))
        row.addWidget(help_button, 0)
        return container

    def _suggested_directory(self) -> str:
        if not self.is_create:
            return ""
        name = self.name_edit.text().strip()
        if self._suggest_dir is not None:
            try:
                return str(self._suggest_dir(name))
            except Exception:
                pass
        if self._base_dir is not None:
            return str(self._base_dir)
        return ""

    def _refresh_suggested_directory(self):
        if not self.is_create or self._dir_manual:
            return
        if not hasattr(self, "path_edit"):
            return
        if self.path_edit.hasFocus():
            return
        path = self._suggested_directory()
        if path and path != self.path_edit.text():
            self.path_edit.setText(path)

    def _on_directory_edited(self, _text):
        self._dir_manual = True

    def _choose_directory(self):
        start = self.path_edit.text().strip()
        if not start and self._base_dir is not None:
            start = str(self._base_dir)
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Каталог проекта",
            start,
        )
        if chosen:
            self._dir_manual = True
            self.path_edit.setText(chosen)

    def _on_accept(self):
        if self.is_create and not self.name_edit.text().strip():
            self.name_edit.setFocus()
            return
        if (
            self.is_create
            and not self.path_edit.text().strip()
            and self._suggest_dir is not None
        ):
            self.path_edit.setText(self._suggested_directory())
        self.accept()

    def values(self):
        """Совместимый набор значений для создания проекта."""
        return (
            self.name_edit.text().strip(),
            [],
            self.save_mode(),
            self.xml_patch_mode(),
        )

    def project_name(self) -> str:
        return self.name_edit.text().strip()

    def project_directory(self) -> str:
        return self.path_edit.text().strip()

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

    def module_setting_values(self):
        """Значения настроек модулей, выбранные в диалоге."""
        result = {}
        for key, (setting, widget) in self._setting_widgets.items():
            kind = str(
                getattr(setting, "kind", "") or "choice"
            ).strip().lower()
            if kind == "choice":
                value = widget.currentData()
                if value is None:
                    value = getattr(setting, "default", None)
            elif kind == "bool":
                value = bool(widget.isChecked())
            else:
                value = str(widget.text())
            result[key] = value
        return result


class ProjectCreateDialog(ProjectSettingsDialog):
    """Создание проекта в едином окне настроек."""

    def __init__(
        self,
        parent=None,
        settings=(),
        setting_values=None,
        base_dir=None,
        suggest_dir=None,
    ):
        super().__init__(
            project=None,
            parent=parent,
            settings=settings,
            setting_values=setting_values,
            base_dir=base_dir,
            suggest_dir=suggest_dir,
        )


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
                "on_file": "On-file",
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
                    "asset": "двоичный ресурс",
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
