# -*- coding: utf-8 -*-
"""Поведение capability ``item.section.dragdrop`` (item_browser)."""

import unittest
from types import SimpleNamespace

from tests.support import SRC  # noqa: F401

from sdk.features.items import ItemSectionData
from modules.item_browser.item_viewer import ItemViewerFeature
from modules.item_browser.module import _ItemSectionDropProvider


class ItemSectionDropCapabilityTest(unittest.TestCase):
    def test_provider_delegates_to_item_viewer(self):
        calls = []

        class Feature:
            def accept_item_section(self, data):
                calls.append(data.section)
                return True

        provider = _ItemSectionDropProvider(Feature())

        self.assertTrue(
            provider.accept_item_section(ItemSectionData("wpn_ak74"))
        )
        self.assertEqual(calls, ["wpn_ak74"])

    def test_accept_opens_card_for_current_document(self):
        feature = object.__new__(ItemViewerFeature)
        shown = []
        cards = []
        feature._tab_id = "base.resolved:system.ltx"
        feature.session = SimpleNamespace(has_tree=True)
        feature._window = None
        feature._pending_drop_section = ""
        feature.show = lambda: shown.append(True)
        feature.open_card = lambda tab_id, section: cards.append(
            (tab_id, section)
        )

        self.assertTrue(
            feature.accept_item_section(ItemSectionData("wpn_ak74"))
        )
        self.assertEqual(shown, [True])
        self.assertEqual(
            cards,
            [("base.resolved:system.ltx", "wpn_ak74")],
        )

    def test_accept_selects_section_without_open_document(self):
        feature = object.__new__(ItemViewerFeature)
        selected = []
        feature._tab_id = ""
        feature.session = SimpleNamespace(has_tree=True)
        feature._window = SimpleNamespace(
            select_section=lambda section: selected.append(section)
            or True
        )
        feature._pending_drop_section = ""
        feature.show = lambda: None

        self.assertTrue(
            feature.accept_item_section(ItemSectionData("wpn_ak74"))
        )
        self.assertEqual(selected, ["wpn_ak74"])
        self.assertEqual(feature._pending_drop_section, "")

    def test_empty_section_is_rejected_without_action(self):
        feature = object.__new__(ItemViewerFeature)
        calls = []
        feature.show = lambda: calls.append("show")
        feature._window = None
        feature._pending_drop_section = ""

        self.assertFalse(
            feature.accept_item_section(ItemSectionData(""))
        )
        self.assertEqual(calls, [])

    def test_missing_tree_is_rejected_without_action(self):
        feature = object.__new__(ItemViewerFeature)
        calls = []
        feature.session = SimpleNamespace(has_tree=False)
        feature._window = None
        feature._pending_drop_section = ""
        feature.show = lambda: calls.append("show")

        self.assertFalse(
            feature.accept_item_section(ItemSectionData("wpn_ak74"))
        )
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
