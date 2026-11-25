# -*- coding: utf-8 -*-
"""Тесты доменных контрактов SDK: documents и patches."""

import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from sdk.contracts.events import Events
from sdk.features.documents import DocumentViewerApi, ResolvedLineEdit
from sdk.features.graph import SectionGraphApi
from sdk.features.items import (
    ItemCatalogApi,
    ItemSectionDropApi,
)
from sdk.features.ogg import OggEditorApi
from sdk.features.patches import PatchRequest, PatchUnavailableError
from sdk.features.projects import ProjectAssetsApi
from sdk.kernel.levels import ApiLevel, api_level
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

    def apply_resolved_line_edit(self, tab_id, action, line_number, text="",
                                 owner=None):
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
            adapter.propose(PatchRequest(target=Path("a.ltx")))


class CapabilityProtocolsTest(unittest.TestCase):
    def test_domain_capability_protocols_are_importable(self):
        for protocol in (
            DocumentViewerApi,
            SectionGraphApi,
            ItemCatalogApi,
            ItemSectionDropApi,
            ProjectAssetsApi,
            OggEditorApi,
        ):
            self.assertTrue(hasattr(protocol, "__mro__"))

    def test_item_api_is_not_kept_as_alias(self):
        import sdk.features.items as items

        self.assertFalse(hasattr(items, "ItemApi"))
        self.assertTrue(hasattr(items, "ItemCatalogApi"))

    def test_capability_protocols_have_explicit_api_levels(self):
        for name in (
            "DocumentViewerApi",
            "SectionGraphApi",
            "ItemCatalogApi",
            "ItemSectionDropApi",
            "ProjectAssetsApi",
            "OggEditorApi",
        ):
            self.assertIs(
                api_level(name),
                ApiLevel.EXPERIMENTAL,
                name,
            )
        self.assertIs(api_level("CapabilitiesApi"), ApiLevel.STABLE)


if __name__ == "__main__":
    unittest.main()
