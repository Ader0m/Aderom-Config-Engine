# -*- coding: utf-8 -*-
"""Платформенный сценарий read-only карточки секции."""

from __future__ import annotations

import logging
import weakref
from pathlib import Path

from app.semantic import build_section_card

from ..section_card import SectionCardDialog


logger = logging.getLogger(__name__)


class SectionCardMixin:
    """Открывает карточку секции и загружает модель в фоновой задаче."""

    def open_section_card(self, tab_id, section):
        """Открыть/поднять карточку секции для resolved-вкладки."""
        if self.window is None:
            return False

        tab_id = str(tab_id or "")
        section = str(section or "").lower()
        if not tab_id or not section:
            return False

        key = (tab_id, section)
        expected = self._section_card_context()
        existing = self._section_cards.get(key)
        if existing is not None:
            if self._section_card_contexts.get(key) == expected:
                existing.show()
                existing.raise_()
                existing.activateWindow()
                return True
            # Проект, снимок или overlay уже другие: старая карточка
            # не должна показывать данные прошлого состояния.
            self._drop_section_card(key, existing)

        document = self._get_or_restore_document(tab_id)
        root_path = document.root_path if document is not None else None
        if root_path is None:
            view = self.window.editor.get_tab(tab_id)
            root_path = getattr(view, "root_path", None)
        if root_path is None:
            self.set_status("Карточка секции: не найден корень вкладки", 5000)
            return False

        root_path = Path(root_path)
        if root_path.suffix.lower() != ".ltx":
            self.set_status(
                "Карточка секции доступна только для LTX-файлов",
                5000,
            )
            return False

        forest = self.workspace.forest
        if forest is None:
            self.set_status(
                "Карточка секции: дерево VFS ещё не построено",
                5000,
            )
            return False

        query = self.services.semantic_query
        config_dir = self.workspace.config_dir or Path(
            getattr(forest, "scan_dir", "") or ""
        )
        overlay = self.project_overlay()

        dialog = SectionCardDialog(section, parent=self.window)
        self._section_cards[key] = dialog
        self._section_card_contexts[key] = expected
        dialog.source_requested.connect(
            self.request_open_file_raw_with_search
        )
        owner_ref = weakref.ref(self)

        def on_destroyed(_obj=None, k=key, ref=owner_ref,
                          expected=dialog):
            owner = ref()
            if owner is None:
                return
            # Старый диалог может быть удалён позже, когда по тому же
            # ключу уже открыта новая карточка. Удаляем только свою.
            if owner._section_cards.get(k) is expected:
                owner._section_cards.pop(k, None)
                owner._section_card_contexts.pop(k, None)

        dialog.destroyed.connect(on_destroyed)
        dialog.set_busy()
        dialog.show()

        def work(log):
            root = query.root(
                root_path,
                forest,
                config_dir,
                overlay=overlay,
                log=log,
            )
            snapshot = root.sections.get(section)
            if snapshot is None:
                return None
            # DTO строится в фоновом потоке: UI получает только простые
            # значения и не перебирает тысячи свойств в UI-потоке.
            return build_section_card(snapshot, root.diagnostics)

        def done(result, error):
            if self._section_cards.get(key) is not dialog:
                return
            if self._section_card_contexts.get(key) != self._section_card_context():
                self._drop_section_card(key, dialog)
                self.set_status(
                    "Карточка секции закрыта: проект, снимок или overlay "
                    "изменился во время загрузки",
                    6000,
                )
                return
            if error is not None:
                logger.error(
                    "Карточка секции %s: ошибка семантического запроса: %s",
                    section,
                    error,
                )
                dialog.set_error(str(error))
                self.log(
                    "Карточка секции %s: %s" % (section, error)
                )
                return

            if result is None:
                dialog.set_missing(
                    "Секция '%s' не найдена в разрешённом корне." % section
                )
                return
            dialog.set_data(result)

        try:
            self.tasks.run(
                work,
                on_done=done,
                on_log=self.log,
                owner=tab_id,
            )
        except Exception as exc:
            self._drop_section_card(key, dialog)
            self.show_error(
                "Не удалось запустить карточку секции:\n%s" % exc
            )
            return False

        return True

    # -------------------------------------------------- контекст и очистка
    def _section_card_context(self):
        """Состояние, к которому привязана карточка."""
        project = self.project
        project_id = (
            str(project.active.id)
            if project.is_active
            else ""
        )
        forest = self.workspace.forest
        snapshot_id = (
            str(getattr(forest, "cache_id", "") or "")
            if forest is not None
            else ""
        )
        overlay = self.project_overlay()
        overlay_revision = str(getattr(overlay, "revision", "") or "")
        return project_id, snapshot_id, overlay_revision

    def _drop_section_card(self, key, dialog):
        if self._section_cards.get(key) is dialog:
            self._section_cards.pop(key, None)
        self._section_card_contexts.pop(key, None)
        dialog.close()

    def close_section_cards(self, tab_id=None):
        """Закрыть карточки вкладки (или все) при смене контекста."""
        wanted = None if tab_id is None else str(tab_id)
        keys = [
            key
            for key in tuple(self._section_cards)
            if wanted is None or key[0] == wanted
        ]
        for key in keys:
            dialog = self._section_cards.get(key)
            if dialog is not None:
                self._drop_section_card(key, dialog)
        return len(keys)


__all__ = ["SectionCardMixin"]
