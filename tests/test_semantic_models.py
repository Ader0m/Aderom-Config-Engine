# -*- coding: utf-8 -*-
"""Модели семантического резолва: неизменяемость и интернирование."""

import dataclasses
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.semantic.models import (
    Diagnostic,
    Diagnostics,
    ModRef,
    OverrideChain,
    RootRef,
    SectionFindPage,
    SectionPage,
    ValueSource,
    ValueSourceInterner,
)


def _source(key="a", operation="set", section="base"):
    return ValueSource(
        file=Path("system.ltx"),
        section=section,
        key=key,
        mod=ModRef("test_mod"),
        priority=0,
        root=RootRef(Path("system.ltx"), "snap"),
        depth=0,
        layer="raw",
        operation=operation,
    )


class SemanticModelsTest(unittest.TestCase):
    def test_value_source_is_frozen(self):
        source = _source()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            source.key = "b"

    def test_override_chain_winner_skips_delete(self):
        chain = OverrideChain(
            (_source("a", "set"), _source("a", "delete"))
        )

        self.assertEqual(len(chain), 2)
        self.assertEqual(chain.operations, ("set", "delete"))
        self.assertEqual(chain.winner.operation, "set")

    def test_override_chain_winner_is_last_append(self):
        chain = OverrideChain(
            (
                _source("list", "set"),
                _source("list", "append"),
                _source("list", "remove"),
            )
        )

        self.assertEqual(chain.winner.operation, "remove")

    def test_interner_reuses_equal_sources(self):
        interner = ValueSourceInterner()
        first = interner.intern(_source())
        second = interner.intern(_source())

        self.assertIs(first, second)
        self.assertEqual(len(interner), 1)

        other = interner.intern(_source(key="b"))
        self.assertIsNot(first, other)
        self.assertEqual(len(interner), 2)

    def test_diagnostics_filter_by_severity(self):
        diagnostics = Diagnostics(
            (
                Diagnostic("info", "i", severity="info"),
                Diagnostic("warn", "w", severity="warning"),
                Diagnostic("error", "e", severity="error"),
            )
        )

        self.assertEqual(len(diagnostics), 3)
        self.assertEqual(
            [item.code for item in diagnostics.warnings],
            ["warn"],
        )
        self.assertEqual(
            [item.code for item in diagnostics.errors],
            ["error"],
        )

    def test_port_exports_semantic_service_and_models(self):
        from core.api import resolve

        for name in (
            "SectionQueryService",
            "ResolvedRoot",
            "SectionSnapshot",
            "PropertySnapshot",
            "ValueSource",
            "OverrideChain",
        ):
            self.assertIn(name, resolve.__all__)
            self.assertTrue(hasattr(resolve, name))

    def test_pages_are_immutable(self):
        page = SectionPage(
            section=None,
            properties=(),
            total=0,
            offset=0,
            limit=10,
        )
        find_page = SectionFindPage(
            names=("a",),
            total=1,
            offset=0,
            limit=10,
        )

        with self.assertRaises(dataclasses.FrozenInstanceError):
            page.total = 1
        with self.assertRaises(dataclasses.FrozenInstanceError):
            find_page.names = ()


if __name__ == "__main__":
    unittest.main()
