# -*- coding: utf-8 -*-
"""Namespace-политика имён событий."""

import unittest

from tests.support import SRC  # noqa: F401

from sdk.contracts.events import Events, is_namespaced, namespace_of


class EventsNamespaceTest(unittest.TestCase):
    def test_every_event_name_is_namespaced(self):
        for name, value in vars(Events).items():
            if name.startswith("_") or not isinstance(value, str):
                continue
            with self.subTest(name=name):
                self.assertTrue(
                    is_namespaced(value),
                    "%s = %r не имеет namespace" % (name, value),
                )

    def test_platform_and_base_prefixes(self):
        self.assertEqual(namespace_of(Events.TREE_READY), "ui")
        self.assertEqual(namespace_of(Events.FILE_OPENED), "ui")
        self.assertEqual(namespace_of(Events.PROJECT_SAVED), "ui")
        self.assertEqual(namespace_of(Events.ITEM_CARD_REQUESTED), "ui")
        self.assertEqual(namespace_of(Events.RESOLVED_DOCUMENT_CHANGED), "ui")
        self.assertEqual(namespace_of(Events.CLEAN_RUN_STARTED), "base")
        self.assertEqual(namespace_of(Events.CLEAN_RUN_FINISHED), "base")
        self.assertEqual(namespace_of(Events.APP_STARTED), "app")
        self.assertEqual(namespace_of(Events.ERROR), "app")


if __name__ == "__main__":
    unittest.main()
