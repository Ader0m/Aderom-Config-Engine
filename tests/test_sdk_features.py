# -*- coding: utf-8 -*-
"""Тесты доменных контрактов SDK: documents и patches."""

import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from sdk.contracts.events import Events
from sdk.features.documents import ResolvedLineEdit
from sdk.features.patches import PatchIntent, PatchUnavailableError
from ui.bus import EventBus
from ui.platform.sdk_bridge import (
    DocumentApiAdapter,
    EventApiAdapter,
    PatchApiAdapter,
)


class _Commands:
    def __init__(self):
        self.calls = []

    def execute(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))


class _Platform:
    def __init__(self):
        self.commands = _Commands()
        self.applied = []

    def document_text(self, path):
        return "text:%s" % Path(path).name

    def apply_resolved_line_edit(self, tab_id, action, line_number, text=""):
        self.applied.append((tab_id, action, line_number, text))
        return True


class DocumentApiAdapterTest(unittest.TestCase):
    def _adapter(self):
        platform = _Platform()
        events = EventApiAdapter(EventBus())
        adapter = DocumentApiAdapter(platform, events=events)
        return platform, events, adapter

    def test_open_read_and_apply(self):
        platform, _events, adapter = self._adapter()

        adapter.open(Path("a.ltx"), line_number=3)
        self.assertEqual(
            platform.commands.calls,
            [("ui.open_file_at_line", (Path("a.ltx"), 3), {})],
        )

        self.assertEqual(adapter.read(Path("a.ltx")), "text:a.ltx")
        edit = ResolvedLineEdit(action="replace", line_number=2, text="x")
        self.assertTrue(adapter.apply("tab", edit))
        self.assertEqual(
            platform.applied,
            [("tab", "replace", 2, "x")],
        )

    def test_subscribe_receives_document_changes(self):
        _platform, events, adapter = self._adapter()
        seen = []
        adapter.subscribe(lambda payload: seen.append(payload))

        events.publish(Events.RESOLVED_DOCUMENT_CHANGED, "changed")

        self.assertEqual(seen, ["changed"])


class PatchApiAdapterTest(unittest.TestCase):
    def test_propose_is_unavailable_until_stage_five(self):
        adapter = PatchApiAdapter()
        with self.assertRaises(PatchUnavailableError):
            adapter.propose(PatchIntent(target=Path("a.ltx")))


if __name__ == "__main__":
    unittest.main()
