# -*- coding: utf-8 -*-
"""Проверки 180-мс задержки поиска по дереву и внутри файла."""

import unittest

from tests.support import SRC  # noqa: F401

from tests.qt_support import qapp
from ui.debounce import Debounce
from ui.editor import EditorFindBar, ResolvedFindBar
from ui.search_bar import SearchBar

_app = qapp()


class _FakeScintilla:
    def __init__(self, text="abc abc"):
        self._text = text
        self.calls = []

    def text(self):
        return self._text

    def findFirst(self, *args):
        self.calls.append(args)
        return True

    def setFocus(self, *args):
        pass


class DebounceTest(unittest.TestCase):
    def test_flush_runs_pending_callback_once(self):
        calls = []
        debounce = Debounce(lambda: calls.append("run"))

        debounce.schedule()
        debounce.schedule()
        self.assertEqual(calls, [])

        self.assertTrue(debounce.flush())
        self.assertEqual(calls, ["run"])
        self.assertFalse(debounce.flush())
        self.assertEqual(calls, ["run"])

    def test_cancel_discards_pending_callback(self):
        calls = []
        debounce = Debounce(lambda: calls.append("run"))

        debounce.schedule()
        debounce.cancel()

        self.assertFalse(debounce.flush())
        self.assertEqual(calls, [])


class SearchBarDebounceTest(unittest.TestCase):
    def test_search_waits_for_debounce(self):
        calls = []
        bar = SearchBar()
        bar.set_search_fn(lambda q: calls.append(q) or [])

        bar.entry.setText("abc")
        self.assertEqual(calls, [])

        bar._debounce.flush()
        self.assertEqual(calls, ["abc"])
        bar.deleteLater()

    def test_enter_flushes_pending_search_first(self):
        calls = []
        bar = SearchBar()
        bar.set_search_fn(lambda q: calls.append(q) or ["a", "b"])

        bar.entry.setText("abc")
        bar.next()

        self.assertEqual(calls, ["abc"])
        self.assertEqual(bar._index, 0)
        bar.deleteLater()

    def test_clearing_is_immediate(self):
        cleared = []
        bar = SearchBar()
        bar.set_search_fn(lambda q: ["a"])
        bar.cleared.connect(lambda: cleared.append(True))

        bar.entry.setText("abc")
        bar.entry.clear()

        self.assertTrue(cleared)
        self.assertEqual(bar.status.text(), "0/0")
        bar.deleteLater()


class EditorFindBarDebounceTest(unittest.TestCase):
    def test_typing_waits_for_debounce(self):
        scintilla = _FakeScintilla()
        bar = EditorFindBar(scintilla)

        bar.entry.setText("abc")
        self.assertEqual(scintilla.calls, [])

        bar._debounce.flush()
        self.assertEqual(len(scintilla.calls), 1)
        bar.deleteLater()

    def test_next_cancels_pending_and_searches_now(self):
        scintilla = _FakeScintilla()
        bar = EditorFindBar(scintilla)

        bar.entry.setText("abc")
        bar.find_next()

        self.assertEqual(len(scintilla.calls), 1)
        self.assertFalse(bar._debounce.is_active())
        bar.deleteLater()


class ResolvedFindBarDebounceTest(unittest.TestCase):
    def test_typing_waits_for_debounce(self):
        scintilla = _FakeScintilla()
        bar = ResolvedFindBar(scintilla)

        bar.entry.setText("abc")
        self.assertEqual(scintilla.calls, [])

        bar._debounce.flush()
        self.assertEqual(len(scintilla.calls), 1)
        bar.deleteLater()


if __name__ == "__main__":
    unittest.main()
