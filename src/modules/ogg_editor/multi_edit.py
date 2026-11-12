# -*- coding: utf-8 -*-
"""Диалог мультиредактирования параметров .ogg."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
)

from .ogg import XraySoundComment
from .sound_types_ui import SoundTypeField


@dataclass(frozen=True)
class MultiEditValues:
    """Значения полей мультиредактора.

    ``None`` означает «значения различаются» (в поле показывается ``-``).
    При применении ``None`` означает «не менять это поле».
    """

    version: Optional[int] = None
    min_dist: Optional[float] = None
    max_dist: Optional[float] = None
    base_volume: Optional[float] = None
    game_type: Optional[int] = None
    max_ai_dist: Optional[float] = None

    def has_any(self) -> bool:
        return any(
            value is not None
            for value in (
                self.version,
                self.min_dist,
                self.max_dist,
                self.base_volume,
                self.game_type,
                self.max_ai_dist,
            )
        )


def common_values(params_list: Iterable[Optional[XraySoundComment]]):
    """Общие значения для выделенных файлов.

    Если хотя бы два файла отличаются по полю, поле становится ``None`` —
    в диалоге оно показывается как ``-``.
    """
    params_list = list(params_list)

    def common(attr):
        values = [
            getattr(params, attr) if params is not None else None
            for params in params_list
        ]
        if not values:
            return None
        first = values[0]
        if all(value == first for value in values):
            return first
        return None

    return MultiEditValues(
        version=common("version"),
        min_dist=common("min_dist"),
        max_dist=common("max_dist"),
        base_volume=common("base_volume"),
        game_type=common("game_type"),
        max_ai_dist=common("max_ai_dist"),
    )


class OptionalDoubleSpinBox(QDoubleSpinBox):
    """Поле, где минимум означает «значение не задано» (``-``)."""

    def stepBy(self, steps):
        if self.value() < 0 and steps > 0:
            self.setValue(0.0)
            return
        if self.value() == 0 and steps < 0:
            self.setValue(self.minimum())
            return
        super().stepBy(steps)


def _normalize_optional(spin):
    if spin.minimum() < spin.value() < 0:
        spin.setValue(spin.minimum())


class MultiEditDialog(QDialog):
    """Одни значения — для всех выбранных файлов."""

    def __init__(self, parent=None, count=0, initial=None, theme_color=None):
        super().__init__(parent)
        self.setWindowTitle("Мультиредактирование .ogg")
        self.resize(540, 370)
        self._apply_dialog_theme(theme_color)

        initial = initial or MultiEditValues()

        layout = QVBoxLayout(self)

        title = QLabel(
            "Будет изменено файлов: %d. Значение «-» означает, что у "
            "выбранных файлов поле различается; если оставить «-», это "
            "поле не изменится." % int(count)
        )
        title.setWordWrap(True)
        layout.addWidget(title)

        form = QFormLayout()

        self.version_combo = QComboBox()
        self.version_combo.addItem("-", None)
        self.version_combo.addItem("1 — min/max/game_type", 1)
        self.version_combo.addItem("2 — + base_volume", 2)
        self.version_combo.addItem("3 — + max_ai_dist", 3)
        index = self.version_combo.findData(initial.version)
        if index < 0:
            index = 0
        self.version_combo.setCurrentIndex(index)
        self.version_combo.currentIndexChanged.connect(
            self._sync_version_fields
        )
        form.addRow("Версия комментария:", self.version_combo)

        self.min_spin = self._spin(initial.min_dist)
        form.addRow("Min distance:", self.min_spin)

        self.max_spin = self._spin(initial.max_dist)
        form.addRow("Max distance:", self.max_spin)

        self.volume_spin = self._spin(initial.base_volume)
        form.addRow("Base volume:", self.volume_spin)

        self.max_ai_spin = self._spin(initial.max_ai_dist)
        form.addRow("Max AI distance:", self.max_ai_spin)

        self.type_combo = SoundTypeField(allow_none=True, none_text="-")
        self.type_combo.set_value(initial.game_type)
        form.addRow("Тип звука:", self.type_combo)

        layout.addLayout(form)

        hint = QLabel(
            "Если версия оставлена как «-», она сохранится у каждого файла. "
            "При изменении base_volume/max_ai_dist версия будет поднята "
            "до 2/3, если это необходимо."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: %s;" % self._muted)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._sync_version_fields()

    def _apply_dialog_theme(self, theme_color):
        """Сразу закрасить фон диалога, не дожидаясь hover-перерисовки."""
        background = "#1E1F22"
        foreground = "#DCDCDC"
        muted = "#9A9A9A"
        border = "#3C3F41"
        selection = "#2E436E"

        if callable(theme_color):
            try:
                background = (
                    theme_color("bg_main", background) or background
                )
                foreground = (
                    theme_color("text", foreground) or foreground
                )
                muted = theme_color("text_muted", muted) or muted
                border = theme_color("border", border) or border
                selection = (
                    theme_color("selection", selection) or selection
                )
            except Exception:
                pass

        self._muted = muted
        self.setObjectName("multiEditDialog")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAutoFillBackground(True)

        palette = self.palette()
        palette.setColor(QPalette.Window, QColor(background))
        palette.setColor(QPalette.Base, QColor(background))
        palette.setColor(QPalette.WindowText, QColor(foreground))
        self.setPalette(palette)

        self.setStyleSheet(
            "QDialog#multiEditDialog {"
            " background-color: %s;"
            "}"
            "QLabel {"
            " background: transparent;"
            " color: %s;"
            "}"
            "QComboBox, QDoubleSpinBox {"
            " background-color: %s;"
            " color: %s;"
            " border: 1px solid %s;"
            "}"
            "QComboBox QAbstractItemView {"
            " background-color: %s;"
            " color: %s;"
            " selection-background-color: %s;"
            "}"
            % (
                background,
                foreground,
                background,
                foreground,
                border,
                background,
                foreground,
                selection,
            )
        )

    @staticmethod
    def _spin(value) -> QDoubleSpinBox:
        spin = OptionalDoubleSpinBox()
        spin.setDecimals(3)
        spin.setRange(-1.0, 1000000.0)
        spin.setSingleStep(0.1)
        spin.setSpecialValueText("-")
        spin.setValue(-1.0 if value is None else float(value))
        spin.editingFinished.connect(
            lambda field=spin: _normalize_optional(field)
        )
        return spin

    @staticmethod
    def _spin_value(spin: QDoubleSpinBox):
        value = float(spin.value())
        return None if value < 0 else value

    def _sync_version_fields(self):
        version = self.version_combo.currentData()
        self.volume_spin.setEnabled(version is None or int(version) >= 2)
        self.max_ai_spin.setEnabled(version is None or int(version) >= 3)

    def values(self) -> MultiEditValues:
        version = self.version_combo.currentData()
        return MultiEditValues(
            version=None if version is None else int(version),
            min_dist=self._spin_value(self.min_spin),
            max_dist=self._spin_value(self.max_spin),
            base_volume=self._spin_value(self.volume_spin),
            game_type=self.type_combo.value(),
            max_ai_dist=self._spin_value(self.max_ai_spin),
        )
