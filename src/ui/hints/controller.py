# -*- coding: utf-8 -*-
"""Контроллер подсказок: состояние, клавиши, popup и вставка."""

from __future__ import annotations

import logging

from PyQt5.QtCore import QEvent, QObject, QPoint, QRect, QTimer, Qt
from PyQt5.QtWidgets import QApplication

from ui.hints.context import build_context
from ui.hints.popup import HintPopup
from ui.hints.ranking import (
    MAX_ITEMS,
    dedupe_items,
    provider_applies,
    result_total,
    validate_result,
)

log = logging.getLogger(__name__)


class HintController(QObject):
    def __init__(
        self,
        service,
        adapter,
        target=None,
        providers=None,
        enabled=True,
        parent=None,
    ):
        super().__init__(parent or adapter.widget())
        self._service = service
        self._adapter = adapter
        self._target = target
        self._providers = tuple(providers) if providers else None
        self._enabled = bool(enabled)
        self._popup = None
        self._result = None
        self._signature = None
        self._context = None
        self._dismissed = None
        self._last_line_text = None
        self._last_column = None
        self._last_revision = None
        self._refreshing = False
        self._suppress = 0
        self._window_filter = False
        self._alive = True

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._refresh)

        widget = self._adapter.widget()
        widget.installEventFilter(self)
        widget.destroyed.connect(self._on_destroyed)
        self._adapter.connect_text_changed(self._schedule)
        self._adapter.connect_cursor_moved(self._schedule)
        self._adapter.connect_scrolled(self.close)
        self._adapter.connect_destroyed(self._on_destroyed)

    # -------------------------------------------------- HintsHandle
    @property
    def enabled(self):
        return self._enabled

    def set_enabled(self, enabled):
        enabled = bool(enabled)
        if enabled == self._enabled:
            return
        self._enabled = enabled
        if not enabled:
            self.close()
        else:
            self._schedule()

    def set_target(self, target):
        self._target = target
        self._dismissed = None
        self._schedule()

    def refresh(self):
        self._schedule()

    def close(self):
        self._hide_popup()
        self._result = None
        self._signature = None
        self._context = None

    def detach(self):
        if not self._alive:
            return
        self._alive = False
        self._timer.stop()
        self.close()
        self._remove_window_filter()
        widget = None
        try:
            widget = self._adapter.widget()
        except Exception:
            widget = None
        if widget is not None:
            try:
                widget.removeEventFilter(self)
            except Exception:
                pass
        if self._popup is not None:
            try:
                self._popup.deleteLater()
            except Exception:
                pass
            self._popup = None
        self.deleteLater()

    def is_active(self):
        if not self._alive or not self._enabled:
            return False
        try:
            return self._adapter.widget() is not None
        except Exception:
            return False

    # -------------------------------------------------- события
    def _schedule(self):
        if not self._alive or not self._enabled or self._suppress:
            return
        self._timer.start()

    def _on_destroyed(self, *_args):
        if not self._alive:
            return
        self._alive = False
        self._timer.stop()
        self._hide_popup()
        self._remove_window_filter()
        if self._popup is not None:
            try:
                self._popup.deleteLater()
            except Exception:
                pass
            self._popup = None
        self._adapter = None
        self.deleteLater()

    def eventFilter(self, obj, event):
        if not self._alive:
            return False
        widget = self._adapter.widget() if self._adapter is not None else None

        if obj is widget:
            event_type = event.type()
            if event_type == QEvent.FocusOut:
                self.close()
                return False
            if event_type == QEvent.KeyPress:
                return self._on_key_press(event)
            if event_type == QEvent.ShortcutOverride:
                return self._on_shortcut_override(event)

        if event.type() == QEvent.MouseButtonPress and self._popup is not None:
            if not self._popup_visible():
                return False
            position = event.globalPos()
            if not self._popup.rect().contains(
                self._popup.mapFromGlobal(position)
            ):
                self.close()
            return False

        if event.type() in (QEvent.WindowDeactivate, QEvent.ApplicationDeactivate):
            self.close()
        if (
            self._popup is not None
            and self._popup_visible()
            and obj is self._popup.parentWidget()
            and event.type() in (QEvent.Resize, QEvent.Move)
        ):
            self.close()
        return False

    def _popup_visible(self):
        if self._popup is None:
            return False
        try:
            return self._popup.isVisible()
        except RuntimeError:
            self._popup = None
            return False

    def _on_key_press(self, event):
        if not self._popup_visible():
            return False
        key = event.key()
        modifiers = event.modifiers()
        blocking = modifiers & (
            Qt.ControlModifier
            | Qt.AltModifier
            | Qt.MetaModifier
            | Qt.ShiftModifier
        )

        if key == Qt.Key_Escape and not blocking:
            self._dismissed = self._signature
            self.close()
            event.accept()
            return True
        if key in (Qt.Key_Up, Qt.Key_Down) and not blocking:
            self._popup.move_selection(-1 if key == Qt.Key_Up else 1)
            event.accept()
            return True
        if key in (Qt.Key_PageUp, Qt.Key_PageDown) and not blocking:
            self._popup.page_selection(-1 if key == Qt.Key_PageUp else 1)
            event.accept()
            return True
        if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Tab) and not blocking:
            self._commit_current()
            event.accept()
            return True
        return False

    def _on_shortcut_override(self, event):
        if not self._popup_visible():
            return False
        key = event.key()
        modifiers = event.modifiers()
        if modifiers & (
            Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier
        ):
            return False
        if key in (
            Qt.Key_Return,
            Qt.Key_Enter,
            Qt.Key_Tab,
            Qt.Key_Escape,
            Qt.Key_Up,
            Qt.Key_Down,
            Qt.Key_PageUp,
            Qt.Key_PageDown,
        ):
            event.accept()
            return True
        return False

    # -------------------------------------------------- пересчёт
    def _refresh(self):
        if not self._alive or not self._enabled or self._refreshing:
            return
        self._refreshing = True
        try:
            self._refresh_impl()
        except Exception:
            log.exception("Ошибка пересчёта подсказок")
            self.close()
        finally:
            self._refreshing = False

    def _refresh_impl(self):
        adapter = self._adapter
        if adapter is None:
            return
        context = build_context(adapter, self._target)
        if not context.editable:
            self.close()
            return

        visible = self._popup_visible()
        previous_line = self._last_line_text
        previous_revision = self._last_revision
        delta = 0 if previous_line is None else len(context.line_text) - len(previous_line)
        self._last_line_text = context.line_text
        self._last_column = context.column
        self._last_revision = context.document_revision
        if (
            previous_line is not None
            and previous_revision is not None
            and context.document_revision != previous_revision
            and abs(delta) > 1
        ):
            if visible:
                self.close()
            return

        result = None
        for candidate in self._service.registry.snapshot():
            if self._providers is not None and candidate not in self._providers:
                continue
            if not provider_applies(candidate, context):
                continue
            try:
                candidate_result = candidate.matches(context)
            except Exception:
                log.exception(
                    "Ошибка провайдера подсказок %s",
                    getattr(candidate, "id", "?"),
                )
                continue
            if candidate_result is None:
                continue
            if not candidate_result.handled and not candidate_result.items:
                continue
            result = candidate_result
            break

        if result is None:
            self.close()
            return
        if not validate_result(result, context):
            log.warning(
                "Провайдер %s вернул некорректный диапазон",
                result.provider_id,
            )
            self.close()
            return

        items = dedupe_items(result.items, limit=MAX_ITEMS)
        if not items:
            self.close()
            return
        total = result_total(result, items)

        signature = (
            context.document_revision,
            context.line,
            context.column,
            result.provider_id,
            context.column - result.replace_before,
            context.column + result.replace_after,
        )
        if signature == self._dismissed:
            self.close()
            return

        previous_insert = None
        if visible and self._popup is not None:
            current = self._popup.current_item()
            if current is not None:
                previous_insert = current.insert_text

        popup = self._ensure_popup()
        if popup is None:
            return
        popup.set_items(items, total)
        if previous_insert:
            popup.select_insert(previous_insert)
        else:
            popup.select_first()

        self._result = result
        self._signature = signature
        self._context = context
        self._install_window_filter()
        anchor = self._anchor_rect()
        popup.show_for(anchor)

    def _anchor_rect(self):
        try:
            x, y, width, height = self._adapter.cursor_rect_global()
        except Exception:
            x = y = 0
            width = 1
            height = 1
        widget = self._adapter.widget()
        window = widget.window()
        point = window.mapFromGlobal(QPoint(int(x), int(y)))
        return QRect(point.x(), point.y(), max(1, width), max(1, height))

    # -------------------------------------------------- вставка
    def _commit_current(self):
        if self._result is None or self._popup is None:
            return
        if self._context is not None:
            try:
                current = build_context(self._adapter, self._target)
            except Exception:
                current = None
            if current is not None and (
                current.document_revision,
                current.line,
                current.column,
            ) != (
                self._context.document_revision,
                self._context.line,
                self._context.column,
            ):
                # Пользователь успел изменить текст/позицию: сначала
                # пересчитываем, вставлять устаревший диапазон нельзя.
                self._refresh()
                return
        item = self._popup.current_item()
        if item is None:
            return
        result = self._result
        text = str(item.insert_text) + str(result.suffix or "")
        caret = int(result.caret_offset or 0)
        keep_open = bool(item.keep_open or result.reopen_after_insert)
        self._suppress += 1
        block = None
        succeeded = False
        try:
            block = self._adapter.begin_edit()
            self._adapter.replace_range(
                result.replace_before,
                result.replace_after,
                text,
                caret,
            )
            succeeded = True
        except Exception:
            log.exception("Не удалось вставить подсказку")
            self.close()
        finally:
            try:
                self._adapter.end_edit(block)
            except Exception:
                log.exception("Не удалось закрыть undo-группу")
            self._suppress = max(0, self._suppress - 1)
        if succeeded:
            # Собственная вставка не должна считаться «массовым
            # изменением»: сбрасываем снимок строки.
            self._last_line_text = None
            self._last_revision = None
        if succeeded and self._alive and keep_open:
            QTimer.singleShot(0, self._schedule)
        else:
            self.close()

    def _commit_row(self, row):
        if self._popup is None:
            return
        index = self._popup.model.index(int(row), 0)
        if index.isValid():
            self._popup.view.setCurrentIndex(index)
        self._commit_current()

    # -------------------------------------------------- popup
    def _ensure_popup(self):
        if self._popup is not None:
            return self._popup
        try:
            window = self._adapter.widget().window()
        except Exception:
            return None
        self._popup = HintPopup(window)
        self._popup.item_activated.connect(self._commit_row)
        return self._popup

    def _hide_popup(self):
        if self._popup is not None:
            try:
                self._popup.hide_popup()
            except RuntimeError:
                self._popup = None
        self._remove_window_filter()

    def _install_window_filter(self):
        if self._window_filter:
            return
        app = QApplication.instance()
        if app is None:
            return
        app.installEventFilter(self)
        self._window_filter = True

    def _remove_window_filter(self):
        if not self._window_filter:
            return
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._window_filter = False
