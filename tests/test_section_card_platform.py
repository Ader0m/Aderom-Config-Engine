# -*- coding: utf-8 -*-
"""Платформенная карточка секции: фоновая загрузка и дедупликация."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.support import SRC  # noqa: F401
from tests.qt_support import qapp
from tests.semantic_support import build_service, write_ltx

from PyQt5.QtCore import QEvent
from PyQt5.QtWidgets import QMainWindow

from core.semantic.query import SectionQueryService
from ui.platform.resolved import ResolvedDocumentMixin
from ui.platform.section_card import SectionCardMixin
from ui.section_card import SectionCardDialog

_app = qapp()


class _Tabs:
    def __init__(self):
        self.widgets = {}

    def get_tab(self, tab_id):
        return self.widgets.get(tab_id)


class _Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.editor = _Tabs()


class _InlineTasks:
    def __init__(self):
        self.calls = []
        self.owners = []

    def run(self, fn, on_done=None, on_log=None, owner=None):
        self.calls.append(fn)
        self.owners.append(owner)
        result = fn(None)
        if on_done is not None:
            on_done(result, None)


class _DeferredTasks:
    def __init__(self):
        self.pending = None

    def run(self, fn, on_done=None, on_log=None, owner=None):
        self.pending = (fn, on_done)

    def complete(self):
        fn, on_done = self.pending
        result = fn(None)
        on_done(result, None)


class _FailingTasks:
    def run(self, fn, on_done=None, on_log=None, owner=None):
        raise RuntimeError("запуск невозможен")


class _Host(SectionCardMixin):
    def __init__(self, root_path, forest, config_dir, service):
        self.window = _Window()
        self.root_path = root_path
        self.workspace = SimpleNamespace(
            forest=forest,
            config_dir=config_dir,
        )
        self.tasks = _InlineTasks()
        self._section_cards = {}
        self._section_card_contexts = {}
        self.project = SimpleNamespace(is_active=False, active=None)
        self.overlay_revision = ""
        self.status = None
        self.error = None
        self.opened = None
        self.services = SimpleNamespace(
            semantic_query=SectionQueryService(service)
        )

    def _get_or_restore_document(self, tab_id):
        return SimpleNamespace(root_path=self.root_path)

    def project_overlay(self):
        if not self.project.is_active:
            return None
        return SimpleNamespace(
            revision=self.overlay_revision,
            source_files={},
            resolved_edits={},
        )

    def set_status(self, message, _timeout=0):
        self.status = message

    def log(self, _message):
        pass

    def show_error(self, message):
        self.error = message

    def request_open_file_raw_with_search(self, path, search_text):
        self.opened = (Path(path), search_text)


class _HookHost(ResolvedDocumentMixin, SectionCardMixin):
    def __init__(self):
        self.window = None
        self._section_cards = {}
        self._section_card_contexts = {}
        self._resolved_documents = {}
        self._patch_preview_ready = False
        self.cancelled = False

    def _cancel_resolved_edits(self):
        self.cancelled = True

    def _clear_pending_file_actions(self):
        pass

    def _apply_patch_action_label(self):
        pass


class SectionCardPlatformTest(unittest.TestCase):
    def _fixture(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        cfg = Path(temp.name) / "configs"
        cfg.mkdir()
        write_ltx(
            cfg / "system.ltx",
            "[base]\na = 1\nlist = x,y\n[child]:base\nc = 3\n",
        )
        write_ltx(cfg / "mod_system_01.ltx", "![base]\na = 10\n")
        forest, service, cache = build_service(cfg)
        self.addCleanup(cache.clear)
        host = _Host(cfg / "system.ltx", forest, cfg, service)
        return cfg, host

    def test_card_loads_in_task_and_reuses_window(self):
        cfg, host = self._fixture()

        self.assertTrue(host.open_section_card("tab-1", "base"))
        dialog = host._section_cards[("tab-1", "base")]
        self.assertIsInstance(dialog, SectionCardDialog)
        self.assertEqual(dialog.table.rowCount(), 2)
        self.assertEqual(len(host.tasks.calls), 1)
        self.assertEqual(host.tasks.owners, ["tab-1"])

        self.assertTrue(host.open_section_card("tab-1", "base"))
        self.assertIs(host._section_cards[("tab-1", "base")], dialog)
        self.assertEqual(len(host.tasks.calls), 1)

    def test_jump_opens_raw_source_with_search(self):
        cfg, host = self._fixture()
        host.open_section_card("tab-1", "base")
        dialog = host._section_cards[("tab-1", "base")]
        dialog.table.selectRow(0)
        dialog._update_jump_button()
        dialog.jump_button.click()

        self.assertEqual(host.opened, (cfg / "mod_system_01.ltx", "a"))

    def test_missing_section_is_shown_without_error(self):
        cfg, host = self._fixture()
        self.assertTrue(host.open_section_card("tab-1", "no_such"))
        dialog = host._section_cards[("tab-1", "no_such")]
        self.assertIn("не найдена", dialog.header.text())

    def test_no_forest_returns_false(self):
        cfg, host = self._fixture()
        host.workspace.forest = None
        self.assertFalse(host.open_section_card("tab-1", "base"))
        self.assertIn("дерево", host.status.lower())

    def test_xml_root_is_rejected(self):
        cfg, host = self._fixture()
        host.root_path = cfg / "a.xml"
        self.assertFalse(host.open_section_card("tab-1", "base"))
        self.assertIn("ltx", host.status.lower())

    def test_overlay_change_reopens_card_with_new_context(self):
        cfg, host = self._fixture()
        host.project.is_active = True
        host.project.active = SimpleNamespace(id="p1")
        host.overlay_revision = "r1"

        self.assertTrue(host.open_section_card("tab-1", "base"))
        first = host._section_cards[("tab-1", "base")]

        host.overlay_revision = "r2"
        self.assertTrue(host.open_section_card("tab-1", "base"))
        second = host._section_cards[("tab-1", "base")]

        self.assertIsNot(first, second)
        self.assertEqual(len(host.tasks.calls), 2)

        # Старый диалог удаляется через event loop: его destroyed не
        # должен выбить новую карточку из реестра.
        qapp().sendPostedEvents(None, QEvent.DeferredDelete)
        qapp().processEvents()
        self.assertIs(host._section_cards[("tab-1", "base")], second)

    def test_tab_close_and_project_change_close_cards(self):
        cfg, host = self._fixture()
        host.open_section_card("tab-1", "base")
        host.open_section_card("tab-2", "base")

        self.assertEqual(host.close_section_cards("tab-1"), 1)
        self.assertNotIn(("tab-1", "base"), host._section_cards)
        self.assertIn(("tab-2", "base"), host._section_cards)

        self.assertEqual(host.close_section_cards(), 1)
        self.assertEqual(host._section_cards, {})
        self.assertEqual(host._section_card_contexts, {})

    def test_project_changed_hook_closes_cards(self):
        host = _HookHost()
        dialog = SectionCardDialog("base")
        key = ("tab-1", "base")
        host._section_cards[key] = dialog
        host._section_card_contexts[key] = ("p1", "s1", "r1")

        host._on_project_changed(None)

        self.assertTrue(host.cancelled)
        self.assertEqual(host._section_cards, {})
        self.assertEqual(host._section_card_contexts, {})

    def test_tab_closed_hook_closes_only_that_tab(self):
        host = _HookHost()
        first = SectionCardDialog("base")
        second = SectionCardDialog("base")
        host._section_cards[("tab-1", "base")] = first
        host._section_cards[("tab-2", "base")] = second
        host._section_card_contexts[("tab-1", "base")] = ("", "", "")
        host._section_card_contexts[("tab-2", "base")] = ("", "", "")

        host._on_tab_closed("tab-1")

        self.assertNotIn(("tab-1", "base"), host._section_cards)
        self.assertIn(("tab-2", "base"), host._section_cards)

    def test_task_start_failure_clears_card_and_context(self):
        cfg, host = self._fixture()
        host.tasks = _FailingTasks()

        self.assertFalse(host.open_section_card("tab-1", "base"))
        self.assertEqual(host._section_cards, {})
        self.assertEqual(host._section_card_contexts, {})
        self.assertIn("не удалось", host.error.lower())

    def test_snapshot_change_during_load_is_not_applied(self):
        cfg, host = self._fixture()
        host.tasks = _DeferredTasks()
        self.assertTrue(host.open_section_card("tab-1", "base"))
        key = ("tab-1", "base")

        old_forest = host.workspace.forest
        host.workspace.forest = SimpleNamespace(
            cache_id="other-snapshot",
            scan_dir=old_forest.scan_dir,
        )
        host.tasks.complete()

        self.assertNotIn(key, host._section_cards)
        self.assertIn("изменился", host.status.lower())

    def test_stale_result_is_not_applied(self):
        cfg, host = self._fixture()
        host.project.is_active = True
        host.project.active = SimpleNamespace(id="p1")
        host.overlay_revision = "r1"
        host.tasks = _DeferredTasks()

        self.assertTrue(host.open_section_card("tab-1", "base"))
        key = ("tab-1", "base")
        dialog = host._section_cards[key]

        host.project.active = SimpleNamespace(id="p2")
        host.tasks.complete()

        self.assertNotIn(key, host._section_cards)
        self.assertIn("изменился", host.status.lower())
        self.assertFalse(dialog.table.isEnabled())


if __name__ == "__main__":
    unittest.main()
