# -*- coding: utf-8 -*-
"""Read-only карточка секции: DTO и таблица свойств."""

import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401
from tests.qt_support import qapp
from tests.semantic_support import build_service, write_ltx

from app.semantic import (
    SectionCardData,
    SectionCardProperty,
    SectionCardSource,
    build_section_card,
)
from core.semantic.models import (
    ModRef,
    SectionSnapshot,
    SourceRef,
)
from core.semantic.query import SectionQueryService
from ui.section_card import SectionCardDialog

_app = qapp()


class SectionCardDataTest(unittest.TestCase):
    def test_section_header_source_keeps_mod(self):
        declaration = SourceRef(
            file=Path("mod_system_01.ltx"),
            section="base",
            mod=ModRef("TestMod"),
        )
        section = SectionSnapshot(
            name="base",
            declaration=declaration,
            kind="override",
            parents=(),
            properties=(),
            source=declaration,
        )

        data = build_section_card(section)

        self.assertEqual(data.declaration.mod, "TestMod")
        self.assertEqual(data.declaration.display, "TestMod / mod_system_01.ltx")

    def test_build_card_truncates_long_value(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "configs"
            cfg.mkdir()
            long_value = "x" * 900
            write_ltx(cfg / "system.ltx", "[base]\nlong = %s\n" % long_value)
            forest, service, cache = build_service(cfg)
            self.addCleanup(cache.clear)
            root = SectionQueryService(service).root(
                cfg / "system.ltx",
                forest,
                cfg,
            )

            data = build_section_card(
                root.sections.get("base"),
                root.diagnostics,
            )
            value = data.properties[0].value

            self.assertLess(len(value), len(long_value))
            self.assertIn("симв.", value)

    def test_build_card_from_semantic_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "configs"
            cfg.mkdir()
            write_ltx(
                cfg / "system.ltx",
                "[base]\na = 1\n[child]:base\nc = 3\n",
            )
            write_ltx(cfg / "mod_system_01.ltx", "![base]\na = 10\n")
            forest, service, cache = build_service(cfg)
            self.addCleanup(cache.clear)
            query = SectionQueryService(service)
            root = query.root(cfg / "system.ltx", forest, cfg)

            data = build_section_card(
                root.sections.get("base"),
                root.diagnostics,
            )
            self.assertEqual(data.name, "base")
            self.assertEqual(data.kind, "override")
            props = {item.key: item for item in data.properties}
            self.assertEqual(props["a"].value, "10")
            self.assertEqual(
                props["a"].source.file.name,
                "mod_system_01.ltx",
            )
            self.assertEqual(
                [item.display for item in props["a"].chain],
                ["system.ltx", "mod_system_01.ltx"],
            )


class SectionCardDialogTest(unittest.TestCase):
    def _data(self):
        source = SectionCardSource(
            file=Path("mod_system_01.ltx"),
            display="mod_system_01.ltx",
            mod=None,
            depth=-200,
            operation="set",
            layer="raw",
        )
        declaration = SectionCardSource(
            file=Path("system.ltx"),
            display="system.ltx",
            mod=None,
            depth=None,
            operation="section",
            layer="raw",
        )
        return SectionCardData(
            name="base",
            kind="override",
            parents=(),
            declaration=declaration,
            source=source,
            properties=(
                SectionCardProperty("a", "10", source, (source,), "exact"),
                SectionCardProperty(
                    "list",
                    "x,y,z",
                    source,
                    (source,),
                    "probable",
                ),
            ),
            diagnostics=(),
        )

    def test_table_filter_and_jump_signal(self):
        dialog = SectionCardDialog("base")
        self.addCleanup(dialog.deleteLater)
        dialog.set_data(self._data())
        self.assertEqual(dialog.table.rowCount(), 2)
        self.assertEqual(dialog.table.item(0, 4).text(), "exact")

        dialog.key_filter.setText("lis")
        self.assertEqual(dialog.table.rowCount(), 1)
        self.assertEqual(dialog.table.item(0, 0).text(), "list")

        dialog.key_filter.clear()
        dialog.source_filter.setText("mod_system")
        self.assertEqual(dialog.table.rowCount(), 2)

        dialog.source_filter.clear()
        dialog.table.selectRow(0)
        dialog._update_jump_button()
        self.assertTrue(dialog.jump_button.isEnabled())

        seen = []
        dialog.source_requested.connect(
            lambda path, search: seen.append((Path(path), search))
        )
        dialog.jump_button.click()
        self.assertEqual(
            seen,
            [(Path("mod_system_01.ltx"), "a")],
        )

    def test_long_value_is_truncated_in_cell(self):
        data = self._data()
        source = data.properties[0].source
        long_value = "x" * 900
        data = SectionCardData(
            name=data.name,
            kind=data.kind,
            parents=data.parents,
            declaration=data.declaration,
            source=data.source,
            properties=(
                SectionCardProperty(
                    "a",
                    long_value,
                    source,
                    (source,),
                    "exact",
                ),
            ),
            diagnostics=data.diagnostics,
        )
        dialog = SectionCardDialog("base")
        self.addCleanup(dialog.deleteLater)
        dialog.set_data(data)
        cell = dialog.table.item(0, 1).text()
        self.assertLess(len(cell), len(long_value))
        self.assertIn("симв.", cell)
        self.assertLess(
            len(dialog._properties[0].value),
            len(long_value),
        )

    def test_large_section_is_paginated(self):
        source = SectionCardSource(
            file=Path("system.ltx"),
            display="system.ltx",
            mod=None,
            depth=0,
            operation="set",
            layer="raw",
        )
        props = tuple(
            SectionCardProperty(
                "k%03d" % index,
                str(index),
                source,
                (source,),
                "exact",
            )
            for index in range(SectionCardDialog.PAGE_SIZE + 25)
        )
        data = SectionCardData(
            name="big",
            kind="base",
            parents=(),
            declaration=source,
            source=source,
            properties=props,
            diagnostics=(),
        )

        dialog = SectionCardDialog("big")
        self.addCleanup(dialog.deleteLater)
        dialog.set_data(data)
        self.assertEqual(
            dialog.table.rowCount(),
            SectionCardDialog.PAGE_SIZE,
        )
        self.assertFalse(dialog.more_button.isHidden())
        dialog.more_button.click()
        self.assertEqual(dialog.table.rowCount(), len(props))
        self.assertTrue(dialog.more_button.isHidden())

    def test_busy_and_missing_states(self):
        dialog = SectionCardDialog("base")
        self.addCleanup(dialog.deleteLater)
        dialog.set_busy("загрузка")
        self.assertFalse(dialog.table.isEnabled())
        self.assertIn("загрузка", dialog.header.text())

        dialog.set_missing("нет секции")
        self.assertIn("нет секции", dialog.header.text())

        dialog.set_error("ошибка")
        self.assertIn("Ошибка", dialog.header.text())


if __name__ == "__main__":
    unittest.main()
