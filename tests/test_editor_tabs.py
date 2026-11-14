# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

from tests.support import SRC  # noqa: F401

from PyQt5.QtWidgets import QMenu, QWidget

from tests.qt_support import qapp
from ui.editor_tabs import EditorTabs

_app = qapp()


class Events:
    def __init__(self):
        self.published = []

    def publish(self, name, payload=None):
        self.published.append((name, payload))


class CodeEditorDebounceTest(unittest.TestCase):
    def test_content_is_emitted_on_flush_not_on_every_change(self):
        editor = EditorTabs()._default_editor
        seen = []
        editor.content_changed.connect(seen.append)
        editor.open_file("a.ltx", text="[a]\nx = 1\n")

        editor.editor.setText("[a]\nx = 2\n")
        self.assertEqual(seen, [])
        editor.flush_pending()
        self.assertEqual(seen[-1], "[a]\nx = 2\n")


class EditorTabsTest(unittest.TestCase):
    def _make(self):
        events = Events()
        tabs = EditorTabs(events=events)
        tabs.test_events = events
        tabs.open_tab("a", "A", QWidget())
        tabs.open_tab("b", "B", QWidget())
        tabs.open_tab("c", "C", QWidget())
        tabs.resize(800, 300)
        tabs.show()
        return tabs

    def _run_menu_action(self, tabs, label, index):
        def fake_exec(menu, *args, **kwargs):
            for action in menu.actions():
                if action.text() == label:
                    return action
            return None

        with patch.object(QMenu, "exec_", fake_exec):
            pos = tabs.tabBar().tabRect(index).center()
            tabs._on_tab_context_menu(pos)

        return [tabs.tabText(i) for i in range(tabs.count())]

    def test_close_tabs_right(self):
        tabs = self._make()
        self.assertEqual(
            self._run_menu_action(tabs, "Закрыть все вкладки справа", 1),
            ["Редактор", "A"],
        )
        closed = {
            payload
            for name, payload in tabs.test_events.published
            if name == "ui.tab.closed"
        }
        self.assertEqual(closed, {"b", "c"})
        tabs.close()

    def test_close_other_tabs(self):
        tabs = self._make()
        self.assertEqual(
            self._run_menu_action(tabs, "Закрыть все другие вкладки", 1),
            ["A"],
        )
        closed = {
            payload
            for name, payload in tabs.test_events.published
            if name == "ui.tab.closed"
        }
        self.assertTrue({"b", "c"}.issubset(closed), closed)
        tabs.close()

    def test_close_all_tabs(self):
        tabs = self._make()
        self.assertEqual(
            self._run_menu_action(tabs, "Закрыть все вкладки", 1),
            [],
        )
        self.assertIsNone(tabs._default_editor)
        closed = {
            payload
            for name, payload in tabs.test_events.published
            if name == "ui.tab.closed"
        }
        self.assertEqual(closed, {"a", "b", "c", "ui.editor"})
        tabs.close()


if __name__ == "__main__":
    unittest.main()
