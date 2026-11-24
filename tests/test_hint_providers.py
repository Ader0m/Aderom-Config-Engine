# -*- coding: utf-8 -*-
"""Провайдеры подсказок на фейковых каталогах."""

import unittest
from types import SimpleNamespace

from tests.support import SRC  # noqa: F401

from core.catalogs.colors import ColorDef, ColorSet
from core.catalogs.includes import IncludeEntry, IncludeSet
from core.catalogs.localization import EMPTY_LOCALIZATION, LocalizationSet
from core.catalogs.sections import SectionInfo, SectionSet
from sdk.ui.hints import HintContext, HintTargetInfo
from ui.platform.hints.providers import (
    ColorTagProvider,
    DirectiveProvider,
    IncludePathProvider,
    KeyProvider,
    LocalizationKeyProvider,
    ParentNameProvider,
    ParentProvider,
    SectionProvider,
)


def context(**overrides):
    values = dict(
        target=HintTargetInfo(surface="raw", language="ltx"),
        line=0,
        column=0,
        line_text="",
        prefix="",
        suffix="",
        editable=True,
        document_revision=1,
    )
    values.update(overrides)
    return HintContext(**values)


def catalogs(**values):
    return SimpleNamespace(
        colors=values.get("colors"),
        sections=values.get("sections"),
        includes=values.get("includes"),
        localization=values.get("localization", EMPTY_LOCALIZATION),
    )


class ColorProviderTest(unittest.TestCase):
    def setUp(self):
        color = ColorDef("ui_gray_3", (170, 170, 170, 255))
        self.provider = ColorTagProvider(
            catalogs(colors=ColorSet(
                by_name={"ui_gray_3": color},
                entries=(color,),
                signature="colors",
            ))
        )

    def test_filters_and_inserts_bracket(self):
        result = self.provider.matches(
            context(
                target=HintTargetInfo(
                    surface="engine_text",
                    language="localization_text",
                ),
                prefix="%c[ui",
                line_text="%c[ui",
                column=5,
                suffix="",
                default_color=(200, 200, 200, 255),
            )
        )
        self.assertTrue(result.handled)
        self.assertEqual([item.label for item in result.items], ["ui_gray_3"])
        self.assertEqual(result.replace_before, 2)
        self.assertEqual(result.suffix, "]")
        self.assertEqual(result.items[0].swatch, (170, 170, 170, 255))

    def test_default_first(self):
        result = self.provider.matches(
            context(
                target=HintTargetInfo(
                    surface="engine_text",
                    language="localization_text",
                ),
                prefix="%c[",
                line_text="%c[",
                column=3,
                default_color=(200, 200, 200, 255),
            )
        )
        self.assertEqual(
            [item.label for item in result.items],
            ["default", "ui_gray_3"],
        )

    def test_consumes_existing_bracket(self):
        result = self.provider.matches(
            context(
                target=HintTargetInfo(
                    surface="engine_text",
                    language="localization_text",
                ),
                prefix="%c[ui",
                suffix="]",
                line_text="%c[ui]",
                column=5,
            )
        )
        self.assertEqual(result.replace_after, 1)


class SectionProvidersTest(unittest.TestCase):
    def setUp(self):
        self.info = SectionInfo(
            name="stalker",
            kind="base",
            path=None,
            line=1,
            keys=("inv_name", "cost"),
            parents=("base",),
        )
        self.set = SectionSet(
            names=("base", "stalker"),
            by_name={
                "base": SectionInfo(name="base"),
                "stalker": self.info,
            },
            unique_keys=("cost", "inv_name"),
        )
        self.catalogs = catalogs(sections=self.set)

    def test_section(self):
        result = SectionProvider(self.catalogs).matches(
            context(prefix="[stal", line_text="[stal", column=5)
        )
        self.assertEqual([item.insert_text for item in result.items], ["stalker"])
        self.assertEqual(result.replace_before, 4)
        self.assertEqual(result.suffix, "]")

    def test_section_preserves_dltx_operator(self):
        result = SectionProvider(self.catalogs).matches(
            context(prefix="![stal", line_text="![stal", column=6)
        )
        self.assertEqual(result.replace_before, 4)

    def test_parent_excludes_current(self):
        result = ParentProvider(self.catalogs).matches(
            context(
                prefix="[stalker]:",
                line_text="[stalker]:",
                column=10,
                section="stalker",
            )
        )
        self.assertEqual(
            [item.insert_text for item in result.items],
            ["base"],
        )

    def test_key(self):
        result = KeyProvider(self.catalogs).matches(
            context(
                prefix="co",
                line_text="co",
                column=2,
                section="stalker",
                local_keys=(("cost", "10"),),
            )
        )
        self.assertEqual(result.items[0].insert_text, "cost")
        self.assertEqual(result.replace_before, 2)


class IncludeProviderTest(unittest.TestCase):
    def test_include_paths(self):
        include_set = IncludeSet(
            entries=(
                IncludeEntry("configs/", True),
                IncludeEntry("configs/defines.ltx", False, ".ltx"),
            ),
            signature="includes",
        )
        provider = IncludePathProvider(
            catalogs(includes=include_set)
        )
        result = provider.matches(
            context(
                prefix='#include "con',
                line_text='#include "con',
                column=13,
                suffix='"',
            )
        )
        self.assertEqual(
            [item.insert_text for item in result.items],
            ["configs/", "configs/defines.ltx"],
        )
        self.assertTrue(result.items[0].keep_open)
        self.assertEqual(result.replace_after, 0)

    def test_directive(self):
        result = DirectiveProvider().matches(
            context(prefix="#inc", line_text="#inc", column=4)
        )
        self.assertEqual(result.items[0].insert_text, "include")
        self.assertEqual(result.suffix, " ")


class LocalizationProviderTest(unittest.TestCase):
    def test_keys_with_preview(self):
        localization = LocalizationSet(
            keys=("st_item_name",),
            lower_keys=("st_item_name",),
            by_lower={"st_item_name": "st_item_name"},
            strings={"st_item_name": "Имя %c[default]"},
            entries={},
            signature="loc",
        )
        provider = LocalizationKeyProvider(
            catalogs(localization=localization)
        )
        result = provider.matches(
            context(
                prefix="inv_name = st",
                line_text="inv_name = st",
                column=13,
            )
        )
        self.assertEqual(result.items[0].insert_text, "st_item_name")
        self.assertEqual(result.items[0].description, "Имя")


class ParentNameProviderTest(unittest.TestCase):
    def test_matches_only_item_parent_role(self):
        first = SectionInfo("wpn_ak74")
        second = SectionInfo("wpn_ak74_base")
        section_set = SectionSet(
            names=("wpn_ak74", "wpn_ak74_base"),
            by_name={
                "wpn_ak74": first,
                "wpn_ak74_base": second,
            },
            unique_keys=(),
        )
        provider = ParentNameProvider(catalogs(sections=section_set))

        target = HintTargetInfo(
            surface="plain",
            language="plain",
            role="item.parent",
        )
        result = provider.matches(context(target=target, prefix="wpn_ak"))
        self.assertEqual(
            [item.insert_text for item in result.items],
            ["wpn_ak74", "wpn_ak74_base"],
        )
        self.assertEqual(result.replace_before, 6)

        other = HintTargetInfo(
            surface="plain",
            language="plain",
            role="other",
        )
        self.assertIsNone(provider.matches(context(target=other, prefix="w")))


class SurfaceRulesTest(unittest.TestCase):
    def test_include_and_directive_only_in_raw(self):
        from ui.platform.hints.ranking import provider_applies

        raw = context(target=HintTargetInfo(surface="raw", language="ltx"))
        resolved = context(
            target=HintTargetInfo(surface="resolved", language="ltx")
        )
        include = IncludePathProvider(catalogs())
        directive = DirectiveProvider()
        # Проверяем правила поверхностей независимо от флага LTX.
        include.enabled = True
        directive.enabled = True
        self.assertTrue(provider_applies(include, raw))
        self.assertTrue(provider_applies(directive, raw))
        self.assertFalse(provider_applies(include, resolved))
        self.assertFalse(provider_applies(directive, resolved))

    def test_ltx_hint_switch_does_not_touch_colors(self):
        from ui.platform.hints.providers.flags import LTX_HINTS_ENABLED
        from ui.platform.hints.ranking import provider_applies

        self.assertTrue(LTX_HINTS_ENABLED)
        raw = context(target=HintTargetInfo(surface="raw", language="ltx"))
        ltx_providers = (
            IncludePathProvider(catalogs()),
            DirectiveProvider(),
            SectionProvider(catalogs()),
            ParentProvider(catalogs()),
            KeyProvider(catalogs()),
            LocalizationKeyProvider(catalogs()),
        )
        for provider in ltx_providers:
            self.assertEqual(
                provider_applies(provider, raw),
                LTX_HINTS_ENABLED,
            )

        engine = context(
            target=HintTargetInfo(
                surface="engine_text",
                language="localization_text",
            )
        )
        self.assertTrue(
            provider_applies(ColorTagProvider(catalogs()), engine)
        )


if __name__ == "__main__":
    unittest.main()
