# -*- coding: utf-8 -*-
"""ItemFeatureService использует общий CacheService и локализацию."""

import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401
from tests.helpers import write_text

from app.features.items import (
    ItemCatalogService,
    ItemFeatureService,
    ItemIconService,
)
from core.cache.service import CacheService
from core.catalogs.localization import LocalizationIndex
from core.localization import LocalizationService


class ItemFeatureCacheTest(unittest.TestCase):
    def test_item_services_share_composition_cache_and_localization(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cache = CacheService()
            localization = LocalizationService(cache_service=cache)
            localization_index = LocalizationIndex()
            resolver = object()

            feature = ItemFeatureService(
                cache_service=cache,
                generated_dir=root / "generated",
                localization=localization,
                localization_index=localization_index,
                resolver=resolver,
            )

            self.assertIs(feature.localization, localization)
            self.assertIs(feature.localization_index, localization_index)
            self.assertIs(feature.catalog._cache_service, cache)
            self.assertIs(feature.icons._cache_service, cache)

            configs = root / "configs"
            write_text(
                configs / "text" / "eng" / "en.xml",
                '<string_table>\n'
                '  <string id="st_name"><text>Name</text></string>\n'
                "</string_table>\n",
            )
            first = feature.localization.load(config_dir=configs)
            second = localization.load(config_dir=configs)

            self.assertIs(first, second)
            self.assertEqual(first.translate("st_name"), "Name")
            stats = cache.stats()["namespaces"]["localization"]
            self.assertEqual(stats["computes"], 1)
            self.assertEqual(stats["hits"], 1)

    def test_item_services_reject_missing_cache_service(self):
        with self.assertRaises(ValueError):
            ItemCatalogService(object())
        with self.assertRaises(ValueError):
            ItemIconService()


if __name__ == "__main__":
    unittest.main()
