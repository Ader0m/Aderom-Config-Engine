# -*- coding: utf-8 -*-
"""Сервис подсказок: реестр, каталоги, подключение к виджетам."""

from __future__ import annotations

import logging
import time
import weakref
from pathlib import Path

from PyQt5.QtCore import QTimer

from core.catalogs import (
    EMPTY_COLORS,
    EMPTY_INCLUDES,
    EMPTY_LOCALIZATION,
    EMPTY_SECTIONS,
)
from sdk.events import Events
from sdk.hints import NullHintsHandle
from ui.hints.adapters import create_adapter
from ui.hints.controller import HintController
from ui.hints.registry import HintRegistry

log = logging.getLogger(__name__)


class HintCatalogs:
    """Снимки каталогов ядра, которые видят провайдеры."""

    def __init__(
        self,
        services,
        forest_getter,
        overlay_getter,
        tasks=None,
        log_fn=None,
        on_changed=None,
    ):
        self._services = services
        self._forest_getter = forest_getter
        self._overlay_getter = overlay_getter
        self._tasks = tasks
        self._log = log_fn or (lambda message: None)
        self._on_changed = on_changed

        self.colors = EMPTY_COLORS
        self.sections = EMPTY_SECTIONS
        self.includes = EMPTY_INCLUDES
        self.localization = EMPTY_LOCALIZATION
        self._pending = set()

    # -------------------------------------------------- состояние
    def _state(self):
        forest = self._forest_getter() if callable(self._forest_getter) else None
        overlay = (
            self._overlay_getter() if callable(self._overlay_getter) else None
        )
        return forest, overlay

    def reset(self):
        self.colors = EMPTY_COLORS
        self.sections = EMPTY_SECTIONS
        self.includes = EMPTY_INCLUDES
        self.localization = EMPTY_LOCALIZATION
        self._pending.clear()

    def _notify_changed(self):
        callback = self._on_changed
        if callable(callback):
            try:
                callback()
            except Exception:
                log.exception("Ошибка обновления после сборки каталога")

    # -------------------------------------------------- сборка
    def refresh_colors(self):
        forest, overlay = self._state()
        if forest is None or self._services is None:
            self.colors = EMPTY_COLORS
            return
        try:
            self.colors = self._services.color_set(forest, overlay=overlay)
        except Exception:
            log.exception("Не удалось собрать каталог цветов")
            self.colors = EMPTY_COLORS

    def refresh_includes(self):
        forest, overlay = self._state()
        if forest is None or self._services is None:
            self.includes = EMPTY_INCLUDES
            return
        try:
            self.includes = self._services.include_set(forest, overlay=overlay)
        except Exception:
            log.exception("Не удалось собрать каталог include-путей")
            self.includes = EMPTY_INCLUDES

    def refresh_localization(self, background=True):
        forest, overlay = self._state()
        if forest is None or self._services is None:
            self.localization = EMPTY_LOCALIZATION
            return
        generation = str(getattr(forest, "cache_id", "") or "")

        def build():
            return self._services.localization_set(
                forest,
                overlay=overlay,
            )

        def apply(value, error):
            self._pending.discard("localization")
            if error is not None:
                log.error("Не удалось собрать индекс локализации: %s", error)
                return
            current = self._forest_getter() if callable(self._forest_getter) else None
            if str(getattr(current, "cache_id", "") or "") != generation:
                return
            if value is not None:
                self.localization = value
                self._notify_changed()

        self._run("localization", build, apply, background)

    def refresh_sections(self, background=True):
        forest, overlay = self._state()
        if forest is None or self._services is None:
            self.sections = EMPTY_SECTIONS
            return
        generation = str(getattr(forest, "cache_id", "") or "")

        def build():
            return self._services.section_set(
                forest,
                overlay=overlay,
                deadline=time.monotonic() + 1.5,
            )

        def apply(value, error):
            self._pending.discard("sections")
            if error is not None:
                log.error("Не удалось собрать каталог секций: %s", error)
                return
            current = self._forest_getter() if callable(self._forest_getter) else None
            if str(getattr(current, "cache_id", "") or "") != generation:
                return
            if value is not None:
                self.sections = value
                self._notify_changed()

        self._run("sections", build, apply, background)

    def refresh_all(self, background=True):
        self.refresh_colors()
        self.refresh_includes()
        self.refresh_localization(background=background)
        self.refresh_sections(background=background)

    def _run(self, key, build, apply, background):
        if key in self._pending:
            return
        self._pending.add(key)
        if background and self._tasks is not None:
            try:
                self._tasks.run(build, on_done=apply)
                return
            except Exception:
                log.exception("Не удалось запустить фоновую сборку %s", key)
        try:
            value = build()
        except Exception as error:
            apply(None, error)
            return
        apply(value, None)


class HintService:
    """Единая точка подключения движка подсказок."""

    def __init__(
        self,
        services,
        events,
        tasks=None,
        forest_getter=None,
        overlay_getter=None,
        log_fn=None,
    ):
        self.registry = HintRegistry()
        self._tasks = tasks
        self._log = log_fn or (lambda message: None)
        self.catalogs = HintCatalogs(
            services,
            forest_getter,
            overlay_getter,
            tasks=tasks,
            log_fn=log_fn,
            on_changed=self.catalogs_changed,
        )
        self._controllers = weakref.WeakSet()
        self._subs = []
        if events is not None:
            self._subs.append(
                events.subscribe(Events.TREE_READY, self._on_tree_ready)
            )
            self._subs.append(
                events.subscribe(Events.PROJECT_CHANGED, self._on_project_changed)
            )
            self._subs.append(
                events.subscribe(Events.PROJECT_SAVED, self._on_project_saved)
            )

    # -------------------------------------------------- регистрация
    def register(self, provider):
        return self.registry.register(provider)

    def unregister(self, provider_id):
        return self.registry.unregister(provider_id)

    def attach(
        self,
        widget,
        target=None,
        providers=None,
        enabled=True,
    ):
        adapter = create_adapter(widget)
        if adapter is None:
            self.log(
                "Подсказки: неподдерживаемый виджет %s"
                % type(widget).__name__
            )
            return NullHintsHandle()
        controller = HintController(
            self,
            adapter,
            target=target,
            providers=providers,
            enabled=enabled,
        )
        self._controllers.add(controller)
        return controller

    # -------------------------------------------------- события
    def _on_tree_ready(self, _forest=None):
        self.catalogs.reset()
        self.catalogs.refresh_all(background=True)
        self._refresh_controllers()

    def _on_project_changed(self, _project=None):
        self.catalogs.reset()
        # Платформа ещё может менять read-only вкладок; обновляем после
        # того, как обработчики события закончат работу.
        QTimer.singleShot(0, lambda: self._after_project_changed())

    def _after_project_changed(self):
        self.catalogs.refresh_all(background=True)
        self._refresh_controllers()

    def _on_project_saved(self, draft=None):
        path = getattr(draft, "path", None)
        if path is None:
            return
        path = Path(path)
        name = path.name.lower()
        suffix = path.suffix.lower()
        kind = str(getattr(draft, "kind", "") or "")
        if name == "color_defs.xml":
            self.catalogs.refresh_colors()
        if suffix == ".xml":
            lowered = [part.lower() for part in path.parts]
            if "text" in lowered:
                self.catalogs.refresh_localization(background=True)
        if suffix == ".ltx" or kind in ("file", "resolved"):
            self.catalogs.refresh_includes()
        self._refresh_controllers()

    def _refresh_controllers(self):
        for controller in list(self._controllers):
            try:
                controller.refresh()
            except Exception:
                log.exception("Не удалось обновить контроллер подсказок")

    def catalogs_changed(self):
        self.registry.notify_catalogs_changed()
        self._refresh_controllers()

    # -------------------------------------------------- прочее
    def log(self, message):
        try:
            self._log(str(message))
        except Exception:
            pass

    def close(self):
        for subscription in self._subs:
            try:
                subscription.unsubscribe()
            except Exception:
                pass
        self._subs.clear()
        for controller in list(self._controllers):
            try:
                controller.detach()
            except Exception:
                pass
