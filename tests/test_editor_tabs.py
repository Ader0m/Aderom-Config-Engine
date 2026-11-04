# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

from tests.support import SRC  # noqa: F401

from PyQt5.QtWidgets import QApplication, QMenu, QWidget

from ui.editor_tabs import EditorTabs

_app = QApplication.instance() or QApplication([])


class Events:
    def publish(self, *args, **kwargs):
        pass


class EditorTabsTest(unittest.TestCase):
    def _make(self):
        tabs = EditorTabs(events=Events())
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
        tabs.close()

    def test_close_other_tabs(self):
        tabs = self._make()
        self.assertEqual(
            self._run_menu_action(tabs, "Закрыть все другие вкладки", 1),
            ["A"],
        )
        tabs.close()

    def test_close_all_tabs(self):
        tabs = self._make()
        self.assertEqual(
            self._run_menu_action(tabs, "Закрыть все вкладки", 1),
            [],
        )
        self.assertIsNone(tabs._default_editor)
        tabs.close()


if __name__ == "__main__":
    unittest.main()
