# -*- coding: utf-8 -*-
"""«Часики» загрузки проекта на вкладках редактора."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.qt_support import qapp

from PyQt5.QtWidgets import QMainWindow, QWidget

from ui.bus import EventBus
from ui.editor import ResolvedView
from ui.editor_tabs import EditorTabs
from ui.platform_api import PlatformApi


class _Window(QMainWindow):
    def __init__(self, events):
        super().__init__()
        self.editor = EditorTabs(events=events)

    def log(self, _message):
        pass

    def log_status(self, _key, _message):
        pass


class _Services:
    def invalidate_paths(self, _roots):
        pass

    def invalidate_resolver_cache(self):
        pass


# Виджеты живут до конца процесса: иначе отложенная покраска лексера
# может сработать уже после удаления C++-объекта вкладки.
_ALIVE = []


def _view():
    view = ResolvedView()
    _ALIVE.append(view)
    return view


class EditorTabsBusyTest(unittest.TestCase):
    def setUp(self):
        self.app = qapp()
        self.tabs = EditorTabs()
        self.addCleanup(self.tabs.deleteLater)

    def test_busy_icon_is_set_and_cleared(self):
        index = self.tabs.indexOf(self.tabs._default_editor)
        self.assertFalse(self.tabs.is_busy)
        self.assertTrue(self.tabs.tabIcon(index).isNull())

        self.tabs.set_busy(True)
        self.assertTrue(self.tabs.is_busy)
        self.assertFalse(self.tabs.tabIcon(index).isNull())

        self.tabs.set_busy(False)
        self.assertFalse(self.tabs.is_busy)
        self.assertTrue(self.tabs.tabIcon(index).isNull())

    def test_tab_opened_while_busy_gets_icon(self):
        self.tabs.set_busy(True)
        widget = QWidget()
        self.tabs.open_tab("module.tab", "Модуль", widget)
        index = self.tabs.indexOf(widget)
        self.assertFalse(self.tabs.tabIcon(index).isNull())

        self.tabs.set_busy(False)
        self.assertTrue(self.tabs.tabIcon(index).isNull())

    def test_individual_busy_survives_global_clear(self):
        widget = QWidget()
        self.tabs.open_tab("stale.tab", "Stale", widget)
        index = self.tabs.indexOf(widget)

        self.tabs.set_busy(True)
        self.tabs.set_tab_busy("stale.tab", True)
        self.tabs.set_busy(False)

        # Вкладка не готова к правке — часики остаются.
        self.assertTrue(self.tabs.is_tab_busy("stale.tab"))
        self.assertFalse(self.tabs.tabIcon(index).isNull())

        self.tabs.set_tab_busy("stale.tab", False)
        self.assertFalse(self.tabs.is_tab_busy("stale.tab"))
        self.assertTrue(self.tabs.tabIcon(index).isNull())

    def test_orphan_resolved_tab_stays_read_only_after_unlock(self):
        view = _view()
        self.tabs.open_tab("resolved.tab", "Resolved", view)

        # Осиротевшая вкладка: редактирование запрещено на уровне вида.
        view.set_editing_supported(False)
        view.set_editable(False)

        # Глобальная разблокировка после загрузки проекта не должна
        # снова разрешить правку в такой вкладке.
        self.tabs.set_editable(True)
        self.assertTrue(view.editor.isReadOnly())

    def test_bound_resolved_tab_becomes_editable(self):
        view = _view()
        self.tabs.open_tab("resolved.ok", "Resolved", view)
        view.set_editing_supported(True)
        self.tabs.set_editable(True)
        self.assertFalse(view.editor.isReadOnly())


class OrphanResolvedBusyTest(unittest.TestCase):
    def setUp(self):
        self.app = qapp()

    def _api(self, root):
        events = EventBus()
        api = PlatformApi(
            events,
            SimpleNamespace(register=lambda *a, **k: None),
            None,
            _Services(),
            SimpleNamespace(
                projects_dir=Path(root) / "projects",
                app_dir=Path(root),
            ),
        )
        window = _Window(events)
        api.window = window
        api.project.create("busy", save_mode="root")
        _ALIVE.append((api, window))
        return api, window

    def test_orphan_resolved_tab_keeps_clock_after_reload(self):
        with tempfile.TemporaryDirectory() as d:
            api, window = self._api(d)
            tab_id = "base.resolved:system.ltx"
            view = _view()
            self.addCleanup(view._lexer_scheduler.stop)
            window.editor.open_tab(tab_id, "[Res]system.ltx", view)

            api._reload_open_documents()

            self.assertTrue(window.editor.is_tab_busy(tab_id))
            self.assertTrue(view.editor.isReadOnly())

    def test_resolved_tab_with_draft_loses_clock(self):
        with tempfile.TemporaryDirectory() as d:
            api, window = self._api(d)
            tab_id = "base.resolved:system.ltx"
            view = _view()
            self.addCleanup(view._lexer_scheduler.stop)
            window.editor.open_tab(tab_id, "[Res]system.ltx", view)
            api.project.update_resolved_draft(
                tab_id,
                Path(d) / "system.ltx",
                "[Res]system.ltx",
                "[sec]\nx = 1\n",
                "[sec]\nx = 2\n",
                (None, None, None),
            )

            api._reload_open_documents()

            self.assertFalse(window.editor.is_tab_busy(tab_id))
            self.assertFalse(view.editor.isReadOnly())


class _RefreshServices(_Services):
    def assemble_resolved(
        self,
        _path,
        _forest,
        _config_dir,
        log=None,
        overlay=None,
    ):
        return "[sec]\nx = 9\n", (None, None, None)


class _SyncTasks:
    """Выполняет фоновую задачу сразу, как будто она уже завершилась."""

    def run(self, fn, on_done=None, on_log=None):
        try:
            result = fn(lambda _message: None)
        except Exception as exc:
            if on_done is not None:
                on_done(None, exc)
            return
        if on_done is not None:
            on_done(result, None)


class _Forest:
    def __init__(self, roots):
        self.roots = [Path(path) for path in roots]
        self.roots_set = set(self.roots)
        self.files = list(self.roots)
        self.xml_files = []
        self.node_parent = {}
        self.include_parents = {}


class OrphanResolvedRefreshTest(unittest.TestCase):
    def setUp(self):
        self.app = qapp()

    def _api(self, root, forest, services=None, tasks=None):
        events = EventBus()
        api = PlatformApi(
            events,
            SimpleNamespace(register=lambda *a, **k: None),
            tasks,
            services or _RefreshServices(),
            SimpleNamespace(
                projects_dir=Path(root) / "projects",
                app_dir=Path(root),
            ),
        )
        window = _Window(events)
        api.window = window
        api.project.create("refresh", save_mode="root")
        api.workspace.set_forest(forest)
        _ALIVE.append((api, window))
        return api, window

    def test_orphan_tab_is_rebuilt_from_project(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "configs" / "system.ltx"
            api, window = self._api(
                d,
                _Forest([root]),
                tasks=_SyncTasks(),
            )
            tab_id = "base.resolved:system.ltx"
            view = _view()
            view.set_content("[old]\n", [None], root)
            self.addCleanup(view._lexer_scheduler.stop)
            window.editor.open_tab(tab_id, "[Res]system.ltx", view)

            api._reload_open_documents()

            self.assertIsNotNone(api.resolved_document(tab_id))
            self.assertFalse(window.editor.is_tab_busy(tab_id))
            self.assertFalse(view.editor.isReadOnly())
            self.assertIn("x = 9", view.text())

    def test_tab_outside_project_stays_busy(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "configs" / "system.ltx"
            other = Path(d) / "configs" / "other.ltx"
            api, window = self._api(
                d,
                _Forest([other]),
                tasks=_SyncTasks(),
            )
            tab_id = "base.resolved:system.ltx"
            view = _view()
            view.set_content("[old]\n", [None], root)
            self.addCleanup(view._lexer_scheduler.stop)
            window.editor.open_tab(tab_id, "[Res]system.ltx", view)

            api._reload_open_documents()

            self.assertTrue(window.editor.is_tab_busy(tab_id))
            self.assertTrue(view.editor.isReadOnly())


if __name__ == "__main__":
    unittest.main()
