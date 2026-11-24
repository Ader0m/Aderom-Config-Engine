# -*- coding: utf-8 -*-
"""Неактивирующий popup подсказок.

Popup — дочерний виджет окна редактора. Он не забирает фокус, поэтому
карточка предмета не выходит из режима правки при клике по строке.
"""

from __future__ import annotations

import logging

from PyQt5.QtCore import QAbstractListModel, QRect, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPixmap
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QLabel,
    QListView,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
)

from ui.platform.theme import theme_service

_ITEM_ROLE = Qt.UserRole + 1

# Размеры одного слота подсказки. Держим в одном месте: делегат,
# расчёт высоты popup и постраничная навигация должны совпадать.
_ROW_HEIGHT = 30
_ROW_HEIGHT_DESCRIPTION = 46
_LABEL_HEIGHT = 22
_SWATCH_SIZE = 16
_MIN_WIDTH = 320
_MAX_WIDTH = 700
_WIDTH_PADDING = 220
_FOOTER_HEIGHT = 20

log = logging.getLogger(__name__)


class HintListModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []
        self._total = 0

    def set_items(self, items, total=0):
        self.beginResetModel()
        try:
            self._items = list(items)
            self._total = int(total or len(self._items))
        except Exception:
            # Не оставляем модель в состоянии reset: иначе view
            # продолжает работать с устаревшими индексами.
            self._items = []
            self._total = 0
            log.exception("Не удалось обновить модель подсказок")
        finally:
            self.endResetModel()

    def rowCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self._items)

    def data(self, index, role=Qt.DisplayRole):
        try:
            if not index.isValid() or not (
                0 <= index.row() < len(self._items)
            ):
                return None
            item = self._items[index.row()]
            if role == Qt.DisplayRole:
                return item.label
            if role == Qt.ToolTipRole:
                return item.description or item.label
            if role == _ITEM_ROLE:
                return item
        except Exception:
            log.exception("Не удалось получить данные подсказки")
        return None

    @property
    def total(self):
        return self._total

    @property
    def items(self):
        return tuple(self._items)

    def item_at(self, row):
        if 0 <= int(row) < len(self._items):
            return self._items[int(row)]
        return None


class HintItemDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._swatches = {}

    def _swatch(self, rgba):
        key = tuple(int(value) & 0xFF for value in rgba)
        if key in self._swatches:
            return self._swatches[key]
        pixmap = QPixmap(_SWATCH_SIZE, _SWATCH_SIZE)
        pixmap.fill(QColor(*key))
        self._swatches[key] = pixmap
        return pixmap

    def sizeHint(self, option, index):
        try:
            item = index.data(_ITEM_ROLE)
        except Exception:
            log.exception("Не удалось прочитать элемент подсказки")
            return QSize(_MIN_WIDTH, _ROW_HEIGHT)
        if item is None:
            return QSize(_MIN_WIDTH, _ROW_HEIGHT)
        height = _ROW_HEIGHT_DESCRIPTION if item.description else _ROW_HEIGHT
        return QSize(_MIN_WIDTH, height)

    def paint(self, painter, option, index):
        # Отрисовка вызывается из C++: исключение здесь = qFatal().
        try:
            self._paint(painter, option, index)
        except Exception:
            log.exception("Не удалось нарисовать строку подсказки")

    def _paint(self, painter, option, index):
        item = index.data(_ITEM_ROLE)
        if item is None:
            return
        painter.save()
        try:
            selected = bool(option.state & QStyle.State_Selected)
            background = QColor(theme_service().palette.accent if selected else theme_service().palette.bg_panel)
            if selected:
                background.setAlpha(70)
            painter.fillRect(option.rect, background)

            rect = option.rect.adjusted(10, 4, -10, -4)
            x = rect.left()
            if item.swatch is not None:
                pixmap = self._swatch(item.swatch)
                painter.drawPixmap(
                    x,
                    rect.top() + max(0, (rect.height() - _SWATCH_SIZE) // 2),
                    pixmap,
                )
                x += _SWATCH_SIZE + 8

            detail_width = 0
            detail = str(item.detail or "")
            if detail:
                detail_font = QFont(option.font)
                detail_font.setPointSizeF(
                    max(7.0, option.font.pointSizeF() - 1.0)
                )
                metrics = QFontMetrics(detail_font)
                detail_width = min(
                    metrics.width(detail) + 12,
                    max(80, rect.width() // 3),
                )
                painter.setFont(detail_font)
                painter.setPen(QColor(theme_service().palette.text_muted))
                painter.drawText(
                    QRect(
                        rect.right() - detail_width,
                        rect.top(),
                        detail_width,
                        rect.height(),
                    ),
                    Qt.AlignRight | Qt.AlignVCenter,
                    metrics.elidedText(
                        detail,
                        Qt.ElideRight,
                        detail_width,
                    ),
                )

            text_width = max(40, rect.right() - x - detail_width - 6)
            label_metrics = QFontMetrics(option.font)
            painter.setFont(option.font)
            painter.setPen(
                QColor(
                    theme_service().palette.text_bright if selected else theme_service().palette.text
                )
            )
            label_rect = QRect(
                x,
                rect.top(),
                text_width,
                _LABEL_HEIGHT if item.description else rect.height(),
            )
            painter.drawText(
                label_rect,
                Qt.AlignLeft | Qt.AlignVCenter,
                label_metrics.elidedText(
                    str(item.label),
                    Qt.ElideRight,
                    text_width,
                ),
            )

            if item.description:
                desc_font = QFont(option.font)
                desc_font.setPointSizeF(
                    max(7.0, option.font.pointSizeF() - 1.0)
                )
                desc_metrics = QFontMetrics(desc_font)
                painter.setFont(desc_font)
                painter.setPen(QColor(theme_service().palette.text_muted))
                painter.drawText(
                    QRect(
                        x,
                        rect.top() + _LABEL_HEIGHT,
                        text_width,
                        rect.height() - _LABEL_HEIGHT,
                    ),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    desc_metrics.elidedText(
                        str(item.description),
                        Qt.ElideRight,
                        text_width,
                    ),
                )
        finally:
            painter.restore()


class HintListView(QListView):
    item_activated = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.NoFocus)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setMouseTracking(True)
        self.setUniformItemSizes(False)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(
            "QListView { background: transparent; border: none; "
            "outline: none; }"
        )
        self.verticalScrollBar().setFocusPolicy(Qt.NoFocus)
        self.clicked.connect(
            lambda index: self.item_activated.emit(index.row())
        )
        self.entered.connect(self.setCurrentIndex)

    def wheelEvent(self, event):
        super().wheelEvent(event)
        event.accept()


class HintPopup(QFrame):
    item_activated = pyqtSignal(int)

    MAX_ROWS = 10

    def __init__(self, parent_window):
        super().__init__(parent_window)
        self.setObjectName("hintPopup")
        self.setFocusPolicy(Qt.NoFocus)
        self.setFrameShape(QFrame.StyledPanel)
        self.setAutoFillBackground(True)
        self.setStyleSheet(
            "QFrame#hintPopup { background: %s; border: 1px solid %s; }"
            % (theme_service().palette.bg_panel, theme_service().palette.border)
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)

        self.model = HintListModel(self)
        self.view = HintListView(self)
        self.view.setModel(self.model)
        self.view.setItemDelegate(HintItemDelegate(self.view))
        self.view.item_activated.connect(self.item_activated.emit)
        layout.addWidget(self.view, 1)

        self.footer = QLabel(self)
        self.footer.setFocusPolicy(Qt.NoFocus)
        self.footer.setStyleSheet(
            "QLabel { color: %s; padding: 2px 8px; }" % theme_service().palette.text_muted
        )
        self.footer.hide()
        layout.addWidget(self.footer, 0)

        self.hide()

    # -------------------------------------------------- данные
    @staticmethod
    def _items_signature(items):
        """Только видимые поля: payload не должен влиять на перерисовку."""
        return tuple(
            (
                str(getattr(item, "label", "")),
                str(getattr(item, "insert_text", "")),
                str(getattr(item, "description", "")),
                str(getattr(item, "detail", "")),
                str(getattr(item, "kind", "")),
                getattr(item, "swatch", None),
            )
            for item in items
        )

    def set_items(self, items, total=0):
        items = list(items)
        total = int(total or len(items))

        # Сброс модели — тяжёлая и потенциально опасная операция для
        # QListView, поэтому не делаем её, если список не изменился.
        same_items = (
            self._items_signature(self.model.items)
            == self._items_signature(items)
        )
        if not same_items or self.model.total != total:
            self.model.set_items(items, total)
            # Текущую строку выставляет контроллер после обновления:
            # setCurrentIndex здесь вызывался дважды за один refresh и
            # мог выполняться внутри обработки события QListView.

        if total > len(items):
            self.footer.setText(
                "Показано %d из %d — уточните ввод" % (len(items), total)
            )
            self.footer.show()
        else:
            self.footer.hide()

        if not items:
            self.hide()
            return

        font = self.view.font()
        metrics = QFontMetrics(font)
        widest = 0
        rows = min(len(items), self.MAX_ROWS)
        height = 0
        for item in items[:rows]:
            widest = max(widest, metrics.width(str(item.label)))
            height += (
                _ROW_HEIGHT_DESCRIPTION
                if item.description
                else _ROW_HEIGHT
            )
        width = max(
            _MIN_WIDTH,
            min(_MAX_WIDTH, widest + _WIDTH_PADDING),
        )
        self.setFixedWidth(width)
        self.setFixedHeight(
            height
            + (_FOOTER_HEIGHT if not self.footer.isHidden() else 2)
        )
        self.view.setMinimumHeight(height)

    def current_item(self):
        index = self.view.currentIndex()
        if not index.isValid():
            return None
        return self.model.item_at(index.row())

    def select_first(self):
        if self.model.rowCount():
            self.view.setCurrentIndex(self.model.index(0, 0))

    def select_insert(self, insert_text):
        wanted = str(insert_text or "")
        for row, item in enumerate(self.model.items):
            if str(item.insert_text) == wanted:
                self.view.setCurrentIndex(self.model.index(row, 0))
                self.view.scrollTo(self.model.index(row, 0))
                return True
        return False

    def move_selection(self, delta):
        count = self.model.rowCount()
        if count <= 0:
            return
        row = self.view.currentIndex().row()
        row = max(0, min(count - 1, row + int(delta)))
        index = self.model.index(row, 0)
        self.view.setCurrentIndex(index)
        self.view.scrollTo(index)

    def page_selection(self, direction):
        row_height = (
            _ROW_HEIGHT_DESCRIPTION
            if any(item.description for item in self.model.items)
            else _ROW_HEIGHT
        )
        rows = max(1, self.view.viewport().height() // row_height)
        self.move_selection(direction * rows)

    def is_visible(self):
        return self.isVisible()

    def show_for(self, anchor):
        """Показать рядом с прямоугольником в координатах окна."""
        parent = self.parentWidget()
        if parent is None:
            self.show()
            return
        bounds = parent.rect()
        width = self.width()
        height = self.height()
        x = int(anchor.x())
        y = int(anchor.y())
        if y + height > bounds.bottom():
            y = int(anchor.y()) - height - int(anchor.height()) - 2
        y = max(0, min(y, bounds.bottom() - height))
        x = max(0, min(x, bounds.right() - width))
        self.move(x, y)
        self.raise_()
        self.show()
        self.view.setFocusPolicy(Qt.NoFocus)

    def hide_popup(self):
        self.hide()
