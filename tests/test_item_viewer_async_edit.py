# -*- coding: utf-8 -*-
"""Асинхронная очередь правок полей карточки предмета."""

import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.support import SRC  # noqa: F401

from tests.qt_support import qapp
from sdk.api import ItemEntry, ItemField, ResolvedDraftInfo
from modules.base_module.item_viewer import ItemCardView, ItemViewerFeature

_app = qapp()


class _Ui:
    def __init__(self):
        self.text = "[item]\nkind = i_food\n"
        self.calls = []
        self.errors = []
        self.logs = []
        self.statuses = []

    def is_project_active(self):
        return True

    def has_resolved_document(self, tab_id):
        return True

    def resolved_draft(self, tab_id):
        return ResolvedDraftInfo(
            text=self.text,
            saved_text="",
            original_text="",
            line_sources=(None, None),
        )

    def apply_resolved_field_edit(self, tab_id, section, key, value):
        self.calls.append((section, key, value))
        self.text = "[item]\n%s = %s\n" % (key, value)
        return True

    def rename_resolved_field(
        self,
        tab_id,
        section,
        old_key,
        new_key,
        value,
    ):
        self.calls.append((section, new_key, value))
        self.text = "[item]\n%s = %s\n" % (new_key, value)
        return True

    def item_entry(self, tab_id, section):
        raise AssertionError("синхронная сборка не должна вызываться")

    def show_error(self, message):
        self.errors.append(str(message))

    def log(self, message):
        self.logs.append(str(message))

    def set_status(self, message, timeout=0):
        self.statuses.append(str(message))


class _Services:
    def build_item_entry(
        self,
        forest,
        text,
        section,
        log=None,
        overlay=None,
    ):
        value = ""
        for line in str(text).split("\n"):
            if "=" in line:
                value = line.split("=", 1)[1].strip()
                break
        return ItemEntry(
            section=section,
            fields=(ItemField("kind", value, 2),),
        )


class _Tasks:
    """Запоминает фоновые задачи, чтобы тест сам их выполнил."""

    def __init__(self):
        self.jobs = []

    def run(self, work, on_done=None, on_log=None):
        self.jobs.append((work, on_done))
        return None

    def flush(self):
        jobs, self.jobs = self.jobs, []
        for work, on_done in jobs:
            try:
                result = work(lambda _message: None)
            except Exception as exc:  # pragma: no cover - защита теста
                if on_done is not None:
                    on_done(None, exc)
                continue
            if on_done is not None:
                on_done(result, None)


class _Ctx:
    def __init__(self, ui, tasks):
        self.ui = ui
        self.tasks = tasks
        self.services = _Services()
        self.events = None


class _Session:
    forest = object()


class _FakeCard:
    def __init__(self, section):
        self._section = section
        self.entries = []

    def section(self):
        return self._section

    def refresh(self, entry=None):
        self.entries.append(entry)


class AsyncFieldQueueTest(unittest.TestCase):
    def setUp(self):
        self.ui = _Ui()
        self.tasks = _Tasks()
        self.feature = ItemViewerFeature(
            _Ctx(self.ui, self.tasks),
            _Session(),
        )
        self.feature._tab_id = "tab"
        self.feature._root_path = Path("system.ltx")
        self.card = _FakeCard("item")
        self.feature._cards["card-tab"] = ("tab", "item", self.card)

    def tearDown(self):
        self.feature.close()

    def test_field_edit_is_applied_before_background_build(self):
        self.assertTrue(
            self.feature._apply_field("tab", "item", "kind", "i_med")
        )

        self.assertEqual(
            self.ui.calls,
            [("item", "kind", "i_med")],
        )
        # Проекция ещё считается в фоне — карточка не обновляется
        # синхронно в UI-потоке.
        self.assertEqual(self.card.entries, [])
        self.assertEqual(len(self.tasks.jobs), 1)

        self.tasks.flush()

        self.assertEqual(len(self.card.entries), 1)
        self.assertEqual(self.card.entries[0].field("kind"), "i_med")

    def test_pending_same_field_edits_are_coalesced(self):
        self.feature._field_job_active = True
        self.assertTrue(
            self.feature._apply_field("tab", "item", "kind", "i_med")
        )
        self.assertTrue(
            self.feature._apply_field("tab", "item", "kind", "i_food")
        )

        self.assertEqual(len(self.feature._field_jobs), 1)
        self.assertEqual(self.ui.calls, [])

        self.feature._field_job_active = False
        self.feature._pump_field_jobs()

        self.assertEqual(
            self.ui.calls,
            [("item", "kind", "i_food")],
        )

    def test_failed_edit_reports_error_and_refreshes(self):
        def fail(*_args, **_kwargs):
            return False

        self.ui.apply_resolved_field_edit = fail

        self.assertTrue(
            self.feature._apply_field("tab", "item", "kind", "i_med")
        )

        self.assertTrue(self.ui.errors)
        self.assertEqual(len(self.tasks.jobs), 1)
        self.tasks.flush()
        self.assertEqual(len(self.card.entries), 1)


class _AsyncUi(_Ui):
    """UI, который принимает правку в очередь и завершает её позже."""

    def __init__(self):
        super().__init__()
        self.pending = []
        self.started = []

    def apply_resolved_field_edit_async(
        self,
        tab_id,
        section,
        key,
        value,
        on_done=None,
    ):
        self.started.append(("field", section, key, value))
        self.pending.append((section, key, value, on_done))
        return True

    def rename_resolved_field_async(
        self,
        tab_id,
        section,
        old_key,
        new_key,
        value,
        on_done=None,
    ):
        self.started.append(("rename", section, new_key, value))
        self.pending.append((section, new_key, value, on_done))
        return True

    def complete_next(self, ok=True):
        section, key, value, on_done = self.pending.pop(0)
        if ok:
            self.text = "[item]\n%s = %s\n" % (key, value)
        on_done(ok, None if ok else "ошибка")


class AsyncQueueBackendTest(unittest.TestCase):
    def setUp(self):
        self.ui = _AsyncUi()
        self.tasks = _Tasks()
        self.feature = ItemViewerFeature(
            _Ctx(self.ui, self.tasks),
            _Session(),
        )
        self.feature._tab_id = "tab"
        self.feature._root_path = Path("system.ltx")
        self.card = _FakeCard("item")
        self.feature._cards["card-tab"] = ("tab", "item", self.card)

    def tearDown(self):
        self.feature.close()

    def test_field_edit_does_not_block_on_platform_save(self):
        self.assertTrue(
            self.feature._apply_field("tab", "item", "kind", "i_med")
        )

        self.assertEqual(self.ui.started, [("field", "item", "kind", "i_med")])
        # Транзакция ещё выполняется в фоне: синхронного применения нет,
        # и пересборка карточки ещё не запускалась.
        self.assertEqual(self.ui.calls, [])
        self.assertEqual(self.tasks.jobs, [])

        self.ui.complete_next(True)

        self.assertEqual(len(self.tasks.jobs), 1)
        self.tasks.flush()
        self.assertEqual(len(self.card.entries), 1)

    def test_next_transaction_waits_for_previous(self):
        self.assertTrue(
            self.feature._apply_field("tab", "item", "kind", "i_med")
        )
        self.assertTrue(
            self.feature._apply_field("tab", "item", "cost", "10")
        )

        # Вторая правка не уходит в платформу, пока первая не завершена.
        self.assertEqual(len(self.ui.pending), 1)
        self.assertEqual(
            [item[2] for item in self.ui.started],
            ["kind"],
        )

        self.ui.complete_next(True)
        self.feature._pump_field_jobs()

        self.assertEqual(
            [item[2] for item in self.ui.started],
            ["kind", "cost"],
        )


class DeferredCardRefreshTest(unittest.TestCase):
    def _entry(self, name="Старое имя"):
        from sdk.api import LocalizationString

        return ItemEntry(
            section="item",
            fields=(ItemField("kind", "i_food", 2),),
            name_text=name,
            name_source=LocalizationString(
                "st_name",
                name,
                Path("text/rus/ru.xml"),
            ),
        )

    def test_card_does_not_rebuild_itself_after_queued_edit(self):
        entry = self._entry()
        calls = []

        def get_entry():
            calls.append(True)
            return entry

        def edit(*_args):
            return True

        card = ItemCardView(
            get_entry=get_entry,
            get_image=lambda section: None,
            can_edit=lambda: True,
            on_field_edit=edit,
            defer_field_refresh=True,
        )
        card.refresh()
        self.assertEqual(len(calls), 1)

        card.fields_table.item(0, 1).setText("i_med")

        # Карточку обновит владелец очереди готовой проекцией, а не
        # синхронный refresh прямо из обработчика ячейки.
        self.assertEqual(len(calls), 1)

    def test_refresh_waits_while_user_is_editing_name(self):
        card = ItemCardView(
            get_entry=lambda: self._entry(),
            get_image=lambda section: None,
            can_edit=lambda: True,
        )
        card.refresh()
        self.assertTrue(card.name_label.begin_editing())
        card.name_label.setText("Пользовательский ввод")

        card.refresh(self._entry("Новое имя"))

        self.assertEqual(card.name_label.text(), "Пользовательский ввод")

        card.name_label.end_editing()
        card._apply_pending_entry()

        self.assertEqual(card.name_label.text(), "Новое имя")


if __name__ == "__main__":
    unittest.main()
