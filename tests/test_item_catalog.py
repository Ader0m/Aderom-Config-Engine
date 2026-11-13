# -*- coding: utf-8 -*-
"""Тесты каталога игровых предметов и координат иконок."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.helpers import write_text
from tests.support import SRC  # noqa: F401

from core.items.catalog import DEFAULT_ICON_TEXTURE, ItemCatalogService


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


class _FakeLocalization:
    def signature(self, forest):
        return "test-localization"

    def load(self, forest, config_dir=None, log=None):
        return self

    def translate(self, value):
        return {
            "Alpha": "Альфа",
            "desc_alpha": "Описание Альфы",
        }.get(value, value)


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
description = desc_alpha
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

    def test_fields_point_to_resolved_lines(self):
        forest = self._forest()
        catalog = ItemCatalogService(
            _FakeResolver(ITEMS_TEXT)
        ).build(forest)
        base = next(
            item for item in catalog.items
            if item.section == "base_item"
        )
        field = base.field("inv_name")
        field_obj = next(
            item for item in base.fields
            if item.key == "inv_name"
        )
        self.assertEqual(field, "Base")
        self.assertGreater(field_obj.line_number, 0)
        self.assertEqual(
            ITEMS_TEXT.splitlines()[field_obj.line_number - 1].strip(),
            "inv_name = Base",
        )

    def test_resolved_text_does_not_call_resolver_twice(self):
        forest = self._forest()

        class _NoResolve(_FakeResolver):
            def assemble_resolved(self, *args, **kwargs):
                raise AssertionError("resolver must not be called")

        catalog = ItemCatalogService(
            _NoResolve("")
        ).build(
            forest,
            resolved_text=ITEMS_TEXT,
            reuse_snapshot_cache=True,
        )
        self.assertTrue(catalog.items)
        self.assertEqual(
            [item.section for item in catalog.items],
            ["alpha_item", "base_item", "beta_item"],
        )

    def test_build_accepts_resolved_text_overlay(self):
        forest = self._forest()
        overlay = ITEMS_TEXT.replace(
            "inv_name = Alpha",
            "inv_name = AlphaChanged",
        )

        catalog = ItemCatalogService(
            _FakeResolver("")
        ).build(forest, resolved_text=overlay)

        alpha = next(
            item for item in catalog.items
            if item.section == "alpha_item"
        )
        self.assertEqual(alpha.field("inv_name"), "AlphaChanged")
        self.assertEqual(alpha.name_text, "AlphaChanged")

    def test_item_gets_localized_name_and_description(self):
        forest = self._forest()
        service = ItemCatalogService(
            _FakeResolver(ITEMS_TEXT),
            localization_service=_FakeLocalization(),
        )

        catalog = service.build(forest)
        alpha = next(
            item for item in catalog.items
            if item.section == "alpha_item"
        )

        self.assertEqual(alpha.name_text, "Альфа")
        self.assertEqual(alpha.description_text, "Описание Альфы")
        self.assertEqual(alpha.field("inv_name"), "Alpha")

    def test_sections_keep_fields_and_parents(self):
        text = (
            "[base_item]\nkind = i_food\ncost = 10\n\n"
            "[child_item]:base_item\ninv_name = Child\n"
        )
        root = write_text(self.items_dir / "items.ltx", text)
        forest = _FakeForest(
            self.configs,
            [root],
            rel_paths={root: "items/items.ltx"},
        )

        catalog = ItemCatalogService(_FakeResolver(text)).build(forest)
        sections = {section.name: section for section in catalog.sections}

        self.assertIn("base_item", sections)
        self.assertEqual(
            sections["base_item"].fields,
            ("kind", "cost"),
        )
        self.assertEqual(
            sections["child_item"].parents,
            ("base_item",),
        )
        self.assertIn("base_item", catalog.parent_names)

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

    # -------------------------------------------------- один предмет
    def test_build_entry_uses_global_line_numbers(self):
        forest = self._forest()
        service = ItemCatalogService(_FakeResolver(ITEMS_TEXT))

        alpha = service.build_entry(forest, ITEMS_TEXT, "alpha_item")

        self.assertIsNotNone(alpha)
        self.assertEqual(alpha.section, "alpha_item")
        self.assertEqual(alpha.field("inv_name"), "Alpha")
        field = next(
            item for item in alpha.fields if item.key == "inv_name"
        )
        self.assertEqual(
            ITEMS_TEXT.splitlines()[field.line_number - 1].strip(),
            "inv_name = Alpha",
        )
        self.assertEqual(
            ITEMS_TEXT.splitlines()[alpha.line_number - 1].strip(),
            "[alpha_item]",
        )

    def test_build_entry_returns_none_for_unknown_or_service(self):
        forest = self._forest()
        service = ItemCatalogService(_FakeResolver(ITEMS_TEXT))
        self.assertIsNone(service.build_entry(forest, ITEMS_TEXT, "nope"))
        self.assertIsNone(
            service.build_entry(forest, ITEMS_TEXT, "tch_hidden")
        )

    def test_build_entry_reflects_edited_resolved_text(self):
        forest = self._forest()
        service = ItemCatalogService(_FakeResolver(ITEMS_TEXT))
        edited = ITEMS_TEXT.replace("kind = i_misc\ncost = 10", "kind = i_med\ncost = 10")

        alpha = service.build_entry(forest, edited, "alpha_item")

        self.assertEqual(alpha.field("kind"), "i_med")
        field = next(
            item for item in alpha.fields if item.key == "kind"
        )
        self.assertEqual(
            edited.splitlines()[field.line_number - 1].strip(),
            "kind = i_med",
        )

    def test_build_entry_uses_last_duplicate_section(self):
        text = (
            "[dup]\ninv_name = First\n\n"
            "[other]\nkind = i_misc\n\n"
            "[dup]\ninv_name = Last\n"
        )
        forest = self._forest()
        service = ItemCatalogService(_FakeResolver(text))

        entry = service.build_entry(forest, text, "dup")

        self.assertEqual(entry.field("inv_name"), "Last")

    def test_build_entry_from_block_offsets_lines(self):
        forest = self._forest()
        service = ItemCatalogService(_FakeResolver(""))
        block = (
            "[alpha_item]\n"
            "inv_name = Alpha\n"
            "kind = i_misc\n"
        )

        entry = service.build_entry_from_block(
            forest,
            block,
            "alpha_item",
            offset=10,
        )

        self.assertEqual(entry.line_number, 11)
        field = next(
            item for item in entry.fields if item.key == "inv_name"
        )
        self.assertEqual(field.line_number, 12)


if __name__ == "__main__":
    unittest.main()
