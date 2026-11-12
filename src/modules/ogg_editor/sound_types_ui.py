# -*- coding: utf-8 -*-
"""Выпадающий справочник типов звука."""

from __future__ import annotations

from PyQt5.QtCore import QEvent, QPoint, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from .sound_types import (
    BASIC_SOUND_TYPES,
    EXTENDED_SOUND_TYPES,
    find_sound_type,
    is_basic_value,
    sound_type_display,
    sound_type_tooltip,
)


_THEME = {
    "bg_main": "#1E1F22",
    "bg_panel": "#2E3033",
    "text": "#DCDCDC",
    "text_muted": "#9A9A9A",
    "text_bright": "#FFFFFF",
    "selection": "#2E436E",
    "border": "#3C3F41",
}


def configure_theme(theme_color):
    """Подтянуть цвета приложения для popup-списков."""
    for key in tuple(_THEME):
        value = theme_color(key)
        if value:
            _THEME[key] = str(value)


def _popup_stylesheet() -> str:
    return (
        "QFrame {"
        " background-color: %(bg_main)s;"
        " border: 1px solid %(border)s;"
        "}"
        "QListWidget {"
        " background-color: %(bg_main)s;"
        " color: %(text)s;"
        " border: none;"
        " outline: none;"
        "}"
        "QListWidget::item {"
        " padding: 6px 8px;"
        "}"
        "QListWidget::item:selected {"
        " background-color: %(selection)s;"
        " color: %(text_bright)s;"
        "}"
        "QLabel {"
        " background-color: %(bg_main)s;"
        " color: %(text_muted)s;"
        "}"
        % _THEME
    )


def _list_font(widget):
    font = widget.font()
    try:
        if font.pointSizeF() > 0:
            font.setPointSizeF(font.pointSizeF() + 1.0)
    except Exception:
        pass
    return font


class SoundTypeCombo(QComboBox):
    """Combo с отдельным списком и фиксированной подписью снизу."""

    valueChanged = pyqtSignal(object)

    def __init__(
        self,
        parent=None,
        allow_none=False,
        none_text="— не задано —",
        entries=None,
        footer_text="Базовые типы",
    ):
        super().__init__(parent)
        self._allow_none = bool(allow_none)
        self._unknown_value = None
        self._none_item = False
        self._footer_text = str(footer_text)
        self._entries = tuple(
            BASIC_SOUND_TYPES if entries is None else entries
        )
        self._popup = None
        self._popup_list = None
        self._popup_footer = None

        if self._allow_none:
            self.addItem(str(none_text), None)
            self._none_item = True

        for entry in self._entries:
            self.addItem(entry.display, entry.value)
            self.setItemData(
                self.count() - 1,
                entry.tooltip,
                3,  # Qt.ToolTipRole
            )

        self.currentIndexChanged.connect(self._on_changed)

    # ------------------------------------------------------------ popup
    def _ensure_popup(self):
        if self._popup is not None:
            return

        popup = QFrame(
            self.window(),
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint,
        )
        popup.setFrameShape(QFrame.StyledPanel)
        popup.setStyleSheet(_popup_stylesheet())

        layout = QVBoxLayout(popup)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        listing = QListWidget(popup)
        listing.setUniformItemSizes(True)
        listing.setFont(_list_font(listing))
        listing.itemClicked.connect(self._choose_item)
        listing.itemActivated.connect(self._choose_item)
        listing.installEventFilter(self)
        layout.addWidget(listing, 1)

        footer = QLabel(self._footer_text, popup)
        footer.setAlignment(Qt.AlignCenter)
        footer.setFont(_list_font(footer))
        layout.addWidget(footer, 0)

        self._popup = popup
        self._popup_list = listing
        self._popup_footer = footer
        self.destroyed.connect(popup.deleteLater)

    def _populate_popup(self):
        listing = self._popup_list
        listing.clear()
        for index in range(self.count()):
            item = QListWidgetItem(self.itemText(index))
            item.setData(Qt.UserRole, index)
            tip = self.itemData(index, 3)
            if tip:
                item.setToolTip(str(tip))
            listing.addItem(item)

        current = self.currentIndex()
        if current >= 0:
            listing.setCurrentRow(current)
            listing.scrollToItem(listing.item(current))

    def _popup_width(self) -> int:
        view = self.view()
        width = view.sizeHintForColumn(0)
        width += view.frameWidth() * 2
        width += self.style().pixelMetric(QStyle.PM_ScrollBarExtent)
        width += 24
        return max(420, self.width(), width)

    def showPopup(self):
        if self._popup is not None and self._popup.isVisible():
            self.hidePopup()
            return

        self._ensure_popup()
        self._populate_popup()

        listing = self._popup_list
        rows = max(1, min(listing.count(), 12))
        row_height = listing.sizeHintForRow(0) or 24
        listing.setFixedHeight(rows * row_height + 6)
        self._popup.setFixedWidth(self._popup_width())
        self._popup.adjustSize()

        anchor = self.mapToGlobal(QPoint(0, self.height()))
        screen = QApplication.screenAt(anchor)
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            x = min(
                max(anchor.x(), available.left()),
                available.right() - self._popup.width(),
            )
            y = anchor.y()
            if y + self._popup.height() > available.bottom():
                y = self.mapToGlobal(QPoint(0, 0)).y() - self._popup.height()
            anchor = QPoint(x, y)

        self._popup.move(anchor)
        self._popup.show()
        self._popup.raise_()
        listing.setFocus()

    def hidePopup(self):
        if self._popup is not None:
            self._popup.hide()
        super().hidePopup()

    def eventFilter(self, obj, event):
        if (
            self._popup is not None
            and obj is self._popup_list
            and event.type() == QEvent.KeyPress
            and event.key() == Qt.Key_Escape
        ):
            self.hidePopup()
            return True
        return super().eventFilter(obj, event)

    def _choose_item(self, item):
        index = item.data(Qt.UserRole)
        if index is None:
            return
        index = int(index)
        self.setCurrentIndex(index)
        self.hidePopup()
        self.activated.emit(index)

    def _on_changed(self, _index):
        self.valueChanged.emit(self.value())

    # ------------------------------------------------------------ значение
    def value(self):
        data = self.currentData()
        return None if data is None else int(data)

    def set_value(self, value, block=True):
        if value is None:
            self._remove_unknown()
            self.setCurrentIndex(0 if self._none_item else -1)
            return

        value = int(value)
        index = self.findData(value)
        if index < 0:
            self._set_unknown(value)
            index = self.findData(value)

        was_blocked = self.blockSignals(block)
        try:
            self.setCurrentIndex(index)
        finally:
            self.blockSignals(was_blocked)

    def _remove_unknown(self):
        if self._unknown_value is None:
            return
        index = self.findData(self._unknown_value)
        if index >= 0:
            was_blocked = self.blockSignals(True)
            try:
                self.removeItem(index)
            finally:
                self.blockSignals(was_blocked)
        self._unknown_value = None

    def _set_unknown(self, value):
        entry = find_sound_type(value)
        if entry is not None:
            text = "%s — вне 21 базового типа" % entry.display
        else:
            text = "%s (нет в справочнике)" % sound_type_display(value)
        self._remove_unknown()
        self._unknown_value = value
        self.addItem(text, value)
        self.setItemData(
            self.count() - 1,
            sound_type_tooltip(value),
            3,
        )


def _info_text() -> str:
    masks = [
        entry for entry in EXTENDED_SOUND_TYPES
        if entry.group == "Маска"
    ]
    bits = [
        entry for entry in EXTENDED_SOUND_TYPES
        if entry.group == "Бит"
    ]

    lines = [
        "Тип звука в OGG-комментарии — это u32-битовая маска.",
        "",
        "Для обычной работы используйте 21 базовый тип:",
        "undefined + 20 готовых комбинированных типов.",
        "",
        "Маски (5):",
    ]
    lines.extend("  " + entry.display for entry in masks)
    lines.extend(["", "Отдельные биты (20):"])
    lines.extend("  " + entry.display for entry in bits)
    lines.extend(
        [
            "",
            "Готовый тип обычно собирается как:",
            "  маска категории | бит действия",
            "",
            "Например:",
            "  Оружие (0x80000000) | Выстрел (0x00200000)",
            "  = 0x80200000 (Weapon shooting)",
            "",
            "Движок проверяет маски через AND. Нестандартная",
            "комбинация может сработать сразу в нескольких проверках",
            "или не сработать ни в одной.",
        ]
    )
    return "\n".join(lines)


class SoundTypeInfoDialog(QDialog):
    """Только для чтения: объяснение масок и битов."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Маски и биты")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.resize(720, 540)
        self.setStyleSheet(
            "QDialog { background-color: %(bg_main)s; }"
            "QPlainTextEdit { background-color: %(bg_main)s;"
            " color: %(text)s; border: 1px solid %(border)s; }"
            % _THEME
        )

        layout = QVBoxLayout(self)
        self.text = QPlainTextEdit(self)
        self.text.setReadOnly(True)
        self.text.setPlainText(_info_text())
        layout.addWidget(self.text, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Close,
            parent=self,
        )
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


_INFO_DIALOG = None


def _forget_info_dialog(*_args):
    global _INFO_DIALOG
    _INFO_DIALOG = None


def show_sound_type_info():
    """Показать независимую немодальную справку.

    Окно не привязано к picker/полю: его можно оставить открытым,
    оно не блокирует приложение и закрывается пользователем или
    вместе с приложением.
    """
    global _INFO_DIALOG

    dialog = _INFO_DIALOG
    if dialog is None:
        dialog = SoundTypeInfoDialog(None)
        dialog.finished.connect(_forget_info_dialog)
        dialog.destroyed.connect(_forget_info_dialog)
        _INFO_DIALOG = dialog

    dialog.show()
    dialog.raise_()
    dialog.activateWindow()


class SoundTypePicker(QFrame):
    """Свободный floating-picker для редактирования ячейки."""

    selected = pyqtSignal(int)

    def __init__(self, parent=None, current_value=None):
        super().__init__(
            parent,
            Qt.Popup | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint,
        )
        self.setStyleSheet(_popup_stylesheet())
        self._current = current_value

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(2)

        self.listing = QListWidget(self)
        self.listing.setUniformItemSizes(True)
        self.listing.setFont(_list_font(self.listing))
        for entry in BASIC_SOUND_TYPES:
            item = QListWidgetItem(entry.display)
            item.setData(Qt.UserRole, entry.value)
            item.setToolTip(entry.tooltip)
            self.listing.addItem(item)
        self.listing.itemClicked.connect(self._choose)

        self.current_label = QLabel(self)
        self.current_label.setWordWrap(True)
        self.current_label.setFont(_list_font(self.current_label))
        self.current_label.setStyleSheet("padding: 4px; color: %s;" % _THEME["text_muted"])
        layout.addWidget(self.current_label, 0)
        layout.addWidget(self.listing, 1)

        footer_row = QHBoxLayout()
        footer = QLabel("Базовые типы", self)
        footer.setAlignment(Qt.AlignCenter)
        footer.setFont(_list_font(footer))
        footer_row.addWidget(footer, 1)

        self.plus_button = QPushButton("?", self)
        self.plus_button.setFixedWidth(28)
        self.plus_button.setToolTip("Справка по маскам и битам")
        self.plus_button.clicked.connect(self._show_info)
        footer_row.addWidget(self.plus_button, 0)
        layout.addLayout(footer_row, 0)

        self._select_current()
        self._update_current_label()
        self.resize(680, 440)

    def _update_current_label(self):
        value = self._current
        if value is None or is_basic_value(value):
            self.current_label.hide()
            return

        entry = find_sound_type(value)
        if entry is not None:
            text = "%s — вне 21 базового типа" % entry.display
        else:
            text = "%s — нет в справочнике" % sound_type_display(value)
        self.current_label.setText("Текущее значение: " + text)
        self.current_label.show()

    def _select_current(self):
        value = self._current
        if value is None:
            return
        for row in range(self.listing.count()):
            if self.listing.item(row).data(Qt.UserRole) == value:
                self.listing.setCurrentRow(row)
                self.listing.scrollToItem(self.listing.item(row))
                return

    def _choose(self, item):
        value = item.data(Qt.UserRole)
        if value is None:
            return
        self.selected.emit(int(value))
        self.hide()

    def _show_info(self):
        show_sound_type_info()

    def show_at(self, point):
        self.adjustSize()
        self.resize(
            max(680, self.width()),
            max(440, self.height()),
        )
        screen = QApplication.screenAt(point)
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            x = min(
                max(point.x(), available.left()),
                available.right() - self.width(),
            )
            y = point.y()
            if y + self.height() > available.bottom():
                y = max(
                    available.top(),
                    point.y() - self.height(),
                )
            point = QPoint(x, y)
        self.move(point)
        self.show()
        self.raise_()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)


class SoundTypeField(QWidget):
    """Базовый combo + кнопка справки о масках и битах."""

    valueChanged = pyqtSignal(object)
    activated = pyqtSignal()

    def __init__(
        self,
        parent=None,
        allow_none=False,
        none_text="— не задано —",
    ):
        super().__init__(parent)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.basic = SoundTypeCombo(
            self,
            allow_none=allow_none,
            none_text=none_text,
            entries=BASIC_SOUND_TYPES,
            footer_text="Базовые типы",
        )
        self.plus_button = QPushButton("?", self)
        self.plus_button.setFixedWidth(24)
        self.plus_button.setToolTip("Справка по маскам и битам")
        self.plus_button.clicked.connect(self._show_info)

        layout.addWidget(self.basic, 1)
        layout.addWidget(self.plus_button, 0)

        self.basic.valueChanged.connect(self.valueChanged.emit)
        self.basic.activated.connect(
            lambda _index: self.activated.emit()
        )

    def _show_info(self):
        show_sound_type_info()

    def set_value(self, value, block=True):
        self.basic.set_value(value, block=block)

    def value(self):
        return self.basic.value()

    def setEnabled(self, enabled):
        super().setEnabled(enabled)
        self.basic.setEnabled(enabled)
        self.plus_button.setEnabled(enabled)


__all__ = [
    "SoundTypeCombo",
    "SoundTypeField",
    "SoundTypeInfoDialog",
    "SoundTypePicker",
    "find_sound_type",
    "show_sound_type_info",
]
