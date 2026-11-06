# -*- coding: utf-8 -*-
"""Тесты каталога игровых предметов и координат иконок."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.helpers import write_text
from tests.support import SRC  # noqa: F401

from core.item_catalog import DEFAULT_ICON_TEXTURE, ItemCatalogService


class _FakeResolver:
    def __init__(self, text, fail=False):
        self.text = text
        self.fail = fail
        self.calls = []

    def assemble_resolved(self, root_path, forest, config_dir, log=None):
        self.calls.append(Path(root_path))
        if self.fail:
            raise RuntimeError("resolver unavailable")
        return self.text, []


class _FakeForest:
    def __init__(self, scan_dir, files, rel_paths=None):
        self.scan_dir = str(scan_dir)
        self.files = [Path(path) for path in files]
        self.rel_paths = {
            Path(path): rel
            for path, rel in (rel_paths or {}).items()
        }


ITEMS_TEXT = """
[base_item]
icons_texture = ui\\custom_atlas
inv_name = Base
kind = i_misc
inv_grid_width = 2

[alpha_item]
inv_name = Alpha
kind = i_misc
cost = 10

[beta_item]
inv_name = Beta
inv_grid_x = 3
inv_grid_y = 4
inv_grid_width = 2
inv_grid_height = 2

[tch_hidden]
inv_name = Hidden
kind = i_quest
"""


class ItemCatalogTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.configs = self.root / "gamedata" / "configs"
        self.items_dir = self.configs / "items"
        self.items_dir.mkdir(parents=True)
        self.texture = (
            self.root / "gamedata" / "textures" / "ui"
            / "ui_icon_equipment.dds"
        )
        self.texture.parent.mkdir(parents=True)
        self.texture.write_bytes(b"")

    def tearDown(self):
        self._tmp.cleanup()

    def _forest(self, name="items.ltx", rel="items/items.ltx"):
        root = write_text(self.items_dir / name, ITEMS_TEXT)
        return _FakeForest(
            self.configs,
            [root],
            rel_paths={root: rel},
        )

    def test_parses_items_and_applies_defaults(self):
        forest = self._forest()
        service = ItemCatalogService(_FakeResolver(ITEMS_TEXT))

        catalog = service.build(forest)

        self.assertEqual(catalog.source_path.name, "items.ltx")
        self.assertEqual(
            [item.section for item in catalog.items],
            ["alpha_item", "base_item", "beta_item"],
        )

        alpha = next(i for i in catalog.items if i.section == "alpha_item")
        self.assertEqual(alpha.field("icons_texture"), DEFAULT_ICON_TEXTURE)
        self.assertEqual(alpha.icon_rect, (0, 0, 50, 50))
        self.assertIsNotNone(alpha.icon_path)

        base = next(i for i in catalog.items if i.section == "base_item")
        self.assertEqual(base.icon_texture, r"ui\custom_atlas")
        self.assertEqual(base.icon_width, 2)
        self.assertEqual(base.icon_height, 1)

        beta = next(i for i in catalog.items if i.section == "beta_item")
        self.assertEqual(beta.icon_rect, (150, 200, 100, 100))
        self.assertEqual(beta.field("inv_grid_x"), "3")

    def test_service_sections_are_skipped(self):
        forest = self._forest()
        catalog = ItemCatalogService(_FakeResolver(ITEMS_TEXT)).build(forest)
        self.assertNotIn(
            "tch_hidden",
            [item.section for item in catalog.items],
        )

    def test_prefers_items_ltx_over_item_base(self):
        items_root = write_text(self.items_dir / "items.ltx", ITEMS_TEXT)
        base_root = write_text(
            self.items_dir / "item_base.ltx",
            "[base_only]\ninv_name = Base\n",
        )
        forest = _FakeForest(
            self.configs,
            [base_root, items_root],
            rel_paths={
                base_root: "items/item_base.ltx",
                items_root: "items/items.ltx",
            },
        )

        catalog = ItemCatalogService(_FakeResolver(ITEMS_TEXT)).build(forest)
        self.assertEqual(catalog.source_path, items_root)

    def test_missing_root_returns_warning(self):
        forest = _FakeForest(self.configs, [])
        catalog = ItemCatalogService(_FakeResolver("")).build(forest)
        self.assertIsNone(catalog.source_path)
        self.assertEqual(catalog.items, ())
        self.assertTrue(catalog.warnings)

    def test_falls_back_to_raw_file_when_resolver_fails(self):
        forest = self._forest()
        service = ItemCatalogService(_FakeResolver("", fail=True))

        catalog = service.build(forest)

        self.assertEqual(
            [item.section for item in catalog.items],
            ["alpha_item", "base_item", "beta_item"],
        )

    def test_catalog_is_cached_by_snapshot(self):
        forest = self._forest()
        resolver = _FakeResolver(ITEMS_TEXT)
        service = ItemCatalogService(resolver)

        first = service.build(forest)
        second = service.build(forest)

        self.assertIs(first, second)
        self.assertEqual(len(resolver.calls), 1)
        self.assertTrue(first.cache_key)

    def test_catalog_cache_survives_new_service(self):
        forest = self._forest()
        cache_dir = self.root / "cache"
        resolver = _FakeResolver(ITEMS_TEXT)

        first = ItemCatalogService(
            resolver,
            cache_dir=cache_dir,
        ).build(forest)

        class _NoResolve(_FakeResolver):
            def assemble_resolved(self, *args, **kwargs):
                raise AssertionError("resolver must not be called")

        second = ItemCatalogService(
            _NoResolve(""),
            cache_dir=cache_dir,
        ).build(forest)

        self.assertEqual(
            [item.section for item in second.items],
            [item.section for item in first.items],
        )
        self.assertEqual(second.cache_key, first.cache_key)


if __name__ == "__main__":
    unittest.main()
