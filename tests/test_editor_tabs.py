# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support import SRC  # noqa: F401

from PyQt5.QtWidgets import QMenu, QMessageBox, QWidget

from sdk.contracts.events import Events as BusEvents, PLATFORM_OWNER
from tests.qt_support import qapp
from ui.editor_tabs import DEFAULT_TAB_ID, EditorTabs

_app = qapp()


class Events:
    def __init__(self):
        self.published = []

    def publish(self, name, payload=None):
        self.published.append((name, payload))


class CodeEditorDebounceTest(unittest.TestCase):
    def test_content_is_emitted_on_flush_not_on_every_change(self):
        tabs = EditorTabs()
        self.addCleanup(tabs.deleteLater)
        editor = tabs.ensure_default_tab()
        seen = []
        editor.content_changed.connect(seen.append)
        editor.open_file("a.ltx", text="[a]\nx = 1\n")

        editor.editor.setText("[a]\nx = 2\n")
        self.assertEqual(seen, [])
        editor.flush_pending()
        self.assertEqual(seen[-1], "[a]\nx = 2\n")

    def test_open_file_reads_text_through_platform_reader(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "a.ltx"
            path.write_text("[original]\n", encoding="utf-8")
            seen = []

            def reader(source):
                seen.append(Path(source))
                return "[from-reader]\nx = 1\n"

            tabs = EditorTabs(text_reader=reader)
            self.addCleanup(tabs.deleteLater)
            editor = tabs.ensure_default_tab()
            self.assertTrue(editor.open_file(path))
            self.assertEqual(seen, [path])
            self.assertIn("x = 1", editor.text())
            self.assertNotIn("[original]", editor.text())

    def test_open_file_without_reader_does_not_read_core(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "a.ltx"
            path.write_text("[a]\n", encoding="utf-8")
            tabs = EditorTabs()
            self.addCleanup(tabs.deleteLater)
            editor = tabs.ensure_default_tab()
            self.assertFalse(editor.open_file(path))


class EditorTabsTest(unittest.TestCase):
    def _make(self):
        events = Events()
        tabs = EditorTabs(events=events)
        tabs.test_events = events
        tabs.ensure_default_tab()
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

    def test_starts_without_tabs(self):
        tabs = EditorTabs()
        self.addCleanup(tabs.deleteLater)
        self.assertEqual(tabs.count(), 0)
        self.assertIsNone(tabs._default_editor)

    def test_close_owner_tabs_closes_only_module_tabs(self):
        tabs = self._make()
        tabs.open_tab("a.tab", "A2", QWidget(), owner="a")
        tabs.open_tab("b.tab", "B2", QWidget(), owner="b")
        tabs.open_tab("c.tab", "C2", QWidget(), owner="c")

        self.assertEqual(tabs.close_owner_tabs("a"), 1)
        self.assertFalse(tabs.has_tab("a.tab"))
        self.assertTrue(tabs.has_tab("b.tab"))
        self.assertTrue(tabs.has_tab("c.tab"))
        self.assertTrue(tabs.has_tab("a"))

        self.assertEqual(tabs.close_owner_tabs("b"), 1)
        self.assertFalse(tabs.has_tab("b.tab"))
        self.assertEqual(tabs.close_owner_tabs("absent"), 0)

    def test_module_cannot_close_foreign_tab(self):
        tabs = EditorTabs()
        self.addCleanup(tabs.deleteLater)
        tabs.open_tab(
            "mod.tab",
            "Module",
            QWidget(),
            owner="mod_a",
        )

        self.assertFalse(tabs.close_tab("mod.tab", owner="mod_b"))
        self.assertTrue(tabs.has_tab("mod.tab"))
        self.assertTrue(tabs.close_tab("mod.tab", owner="mod_a"))
        self.assertFalse(tabs.has_tab("mod.tab"))

    def test_owner_is_explicit_not_derived_from_tab_id(self):
        tabs = EditorTabs()
        self.addCleanup(tabs.deleteLater)
        tabs.open_tab(
            "mod_a.fake",
            "Fake",
            QWidget(),
            owner="mod_b",
        )

        self.assertEqual(tabs.close_owner_tabs("mod_a"), 0)
        self.assertTrue(tabs.has_tab("mod_a.fake"))
        self.assertEqual(tabs.close_owner_tabs("mod_b"), 1)
        self.assertFalse(tabs.has_tab("mod_a.fake"))

    def test_module_cannot_change_foreign_tab_state(self):
        tabs = EditorTabs()
        self.addCleanup(tabs.deleteLater)
        tabs.open_tab(
            "mod.tab",
            "Module",
            QWidget(),
            owner="mod_a",
        )

        self.assertFalse(
            tabs.set_tab_dirty("mod.tab", True, owner="mod_b")
        )
        self.assertFalse(
            tabs.set_tab_busy("mod.tab", True, owner="mod_b")
        )
        self.assertTrue(
            tabs.set_tab_dirty("mod.tab", True, owner="mod_a")
        )
        self.assertTrue(
            tabs.set_tab_busy(
                "mod.tab",
                True,
                owner=PLATFORM_OWNER,
            )
        )
        self.assertTrue(
            tabs.close_tab("mod.tab", owner=PLATFORM_OWNER)
        )

    def test_reserved_default_tab_id_returns_false(self):
        tabs = EditorTabs()
        self.addCleanup(tabs.deleteLater)

        self.assertFalse(
            tabs.open_tab(
                DEFAULT_TAB_ID,
                "Reserved",
                QWidget(),
                owner="mod_a",
            )
        )
        self.assertIsNone(tabs.get_tab(DEFAULT_TAB_ID))

    def test_module_cannot_write_default_platform_tab(self):
        tabs = EditorTabs()
        self.addCleanup(tabs.deleteLater)
        editor = tabs.ensure_default_tab()
        editor.show_text("platform\n", "Platform")
        self.assertFalse(
            tabs.show_text(
                "[foreign]\n",
                "Foreign",
                owner="mod_x",
            )
        )
        self.assertEqual(editor.text(), "platform\n")
        self.assertFalse(
            tabs.show_text(
                "[foreign]\n",
                "Foreign",
                tab_id="ui.editor",
                owner="mod_x",
            )
        )
        self.assertEqual(editor.text(), "platform\n")
        self.assertTrue(
            tabs.show_text("[platform]\n", "Platform", owner=None)
        )
        self.assertEqual(editor.text(), "[platform]\n")

    def test_ask_if_dirty_close_policy(self):
        tabs = EditorTabs()
        self.addCleanup(tabs.deleteLater)
        tabs.open_tab(
            "dirty.tab",
            "Dirty",
            QWidget(),
            owner="demo",
        )
        tabs.set_tab_dirty("dirty.tab", True)
        tabs.set_tab_close_policy("dirty.tab", "ask_if_dirty")
        index = tabs.indexOf(tabs.get_tab("dirty.tab"))

        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.No,
        ):
            tabs._on_close_requested(index)
        self.assertTrue(tabs.has_tab("dirty.tab"))

        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.Yes,
        ):
            tabs._on_close_requested(index)
        self.assertFalse(tabs.has_tab("dirty.tab"))

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


class EditorTabsOpenPublishTest(unittest.TestCase):
    def setUp(self):
        self.app = qapp()

    def _tabs(self):
        events = Events()
        tabs = EditorTabs(events=events)
        self.addCleanup(tabs.deleteLater)
        return tabs, events

    def test_open_file_failure_does_not_publish_file_opened(self):
        tabs, events = self._tabs()
        tabs.ensure_default_tab()
        with patch.object(
            tabs._default_editor,
            "open_file",
            side_effect=OSError("read failed"),
        ):
            self.assertFalse(
                tabs.open_file("a.ltx", text="[a]\n", request_id=3)
            )

        opened = [
            payload
            for name, payload in events.published
            if name == BusEvents.FILE_OPENED
        ]
        self.assertEqual(opened, [])

    def test_open_file_publishes_request_id(self):
        tabs, events = self._tabs()
        self.assertTrue(tabs.open_file("a.ltx", text="[a]\n", request_id=7))

        opened = [
            payload
            for name, payload in events.published
            if name == BusEvents.FILE_OPENED
        ]
        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0].request_id, 7)
        self.assertEqual(Path(opened[0].path), Path("a.ltx"))

    def test_show_text_without_source_does_not_publish_file_opened(self):
        tabs, events = self._tabs()
        tabs.show_text("[a]\n", "Текст без файла", request_id=5)

        opened = [
            payload
            for name, payload in events.published
            if name == BusEvents.FILE_OPENED
        ]
        self.assertEqual(opened, [])

    def test_named_show_text_opens_separate_tabs(self):
        tabs, events = self._tabs()
        tabs.show_text(
            "[a]\n",
            "A",
            source_path="a.ltx",
            tab_id="base.raw:a.ltx",
            request_id=1,
        )
        tabs.show_text(
            "[b]\n",
            "B",
            source_path="b.ltx",
            tab_id="base.raw:b.ltx",
            request_id=2,
        )

        first = tabs.get_tab("base.raw:a.ltx")
        second = tabs.get_tab("base.raw:b.ltx")
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNot(first, second)
        self.assertEqual(first.text(), "[a]\n")
        self.assertEqual(second.text(), "[b]\n")

        opened = [
            payload
            for name, payload in events.published
            if name == BusEvents.FILE_OPENED
        ]
        self.assertEqual(
            [(item.tab_id, item.request_id) for item in opened],
            [("base.raw:a.ltx", 1), ("base.raw:b.ltx", 2)],
        )

    def test_named_show_text_reuses_its_tab(self):
        tabs, events = self._tabs()
        tabs.show_text(
            "[a]\n",
            "A",
            source_path="a.ltx",
            tab_id="base.raw:a.ltx",
        )
        first = tabs.get_tab("base.raw:a.ltx")

        tabs.show_text(
            "[a2]\n",
            "A2",
            source_path="a.ltx",
            tab_id="base.raw:a.ltx",
        )

        self.assertIs(tabs.get_tab("base.raw:a.ltx"), first)
        self.assertEqual(first.text(), "[a2]\n")
        self.assertEqual(tabs.tabText(tabs.indexOf(first)), "A2")


if __name__ == "__main__":
    unittest.main()
