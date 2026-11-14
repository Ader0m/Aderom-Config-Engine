# -*- coding: utf-8 -*-
"""Название и описание предмета: правка через XML строковой таблицы."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from tests.helpers import write_text
from tests.support import SRC  # noqa: F401

from tests.qt_support import qapp
from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QLineEdit
from sdk.api import ItemEntry, ItemField, LocalizationString, ProjectOverlay
from core.items.catalog import ItemCatalogService
from core.items.localization import (
    LocalizationService,
    edit_localization_text,
)
from modules.base_module.item_viewer import ItemCardView, ItemViewerFeature

_app = qapp()


RU_XML = """<?xml version="1.0" encoding="windows-1251"?>
<string_table>
\t<string id="st_name"><text>Старое имя</text></string>
\t<string id="st_desc"><text>Старое описание</text></string>
</string_table>
"""

ITEMS = """[item]
inv_name = st_name
description = st_desc
kind = i_food
"""


class _Resolver:
    def assemble_resolved(self, root_path, forest, config_dir, log=None):
        return ITEMS, []


class _Forest:
    def __init__(self, scan_dir):
        self.scan_dir = str(scan_dir)
        self.files = []
        self.xml_files = []


class EditLocalizationTextTest(unittest.TestCase):
    def test_updates_only_selected_string_and_escapes_xml(self):
        updated = edit_localization_text(
            RU_XML,
            "st_name",
            "Новое & <опасное>",
        )

        self.assertIn(
            '<string id="st_name"><text>'
            'Новое &amp; &lt;опасное&gt;</text></string>',
            updated,
        )
        self.assertIn("<text>Старое описание</text>", updated)

    def test_newlines_are_written_as_engine_sequence(self):
        updated = edit_localization_text(RU_XML, "st_desc", "A\nB")
        # Литерал ``\n`` остаётся движковым переносом, а после него
        # появляется реальный перенос XML для читаемости.
        self.assertIn("A\\n\n\t\tB", updated)
        self.assertIn("B</text>", updated)

    def test_missing_key_is_appended_before_table_end(self):
        updated = edit_localization_text(RU_XML, "st_new", "Новый текст")
        self.assertIn('<string id="st_new"><text>Новый текст</text>',
                      updated)
        self.assertLess(
            updated.index('id="st_new"'),
            updated.index("</string_table>"),
        )

    def test_non_string_table_is_rejected(self):
        self.assertIsNone(edit_localization_text("<root/>", "st_name", "x"))


class LocalizationOverlayTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.configs = Path(self._tmp.name) / "gamedata" / "configs"
        self.ru = write_text(
            self.configs / "text" / "rus" / "ru.xml",
            RU_XML,
        )

    def test_overlay_changes_displayed_text_and_source(self):
        edited = edit_localization_text(RU_XML, "st_name", "Новое имя")
        overlay = ProjectOverlay(source_files={self.ru: edited})

        table = LocalizationService().load(
            config_dir=self.configs,
            overlay=overlay,
        )

        self.assertEqual(table.translate("st_name"), "Новое имя")
        entry = table.entry("st_name")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.path, self.ru)
        self.assertEqual(entry.text, "Новое имя")

    def test_overlay_new_file_is_loaded_in_language(self):
        new_path = self.configs / "text" / "rus" / "st_ace.xml"
        overlay = ProjectOverlay(
            source_files={
                new_path: (
                    '<string_table>\n'
                    '\t<string id="st_new"><text>Из проекта</text></string>\n'
                    "</string_table>\n"
                )
            },
            new_files=frozenset({new_path}),
        )

        table = LocalizationService().load(
            config_dir=self.configs,
            overlay=overlay,
        )

        self.assertEqual(table.translate("st_new"), "Из проекта")
        self.assertEqual(table.entry("st_new").path, new_path)

    def test_included_fragment_is_reported_as_source(self):
        part = write_text(
            self.configs / "text" / "rus" / "part.xml",
            '<string id="st_part"><text>Часть</text></string>\n',
        )
        write_text(
            self.configs / "text" / "rus" / "ru.xml",
            '<string_table>\n#include "part.xml"\n</string_table>\n',
        )

        table = LocalizationService().load(config_dir=self.configs)

        self.assertEqual(table.translate("st_part"), "Часть")
        self.assertEqual(table.entry("st_part").path, part)

    def test_catalog_entry_uses_overlay_text(self):
        edited = edit_localization_text(RU_XML, "st_name", "Имя проекта")
        overlay = ProjectOverlay(source_files={self.ru: edited})
        forest = _Forest(self.configs)

        entry = ItemCatalogService(
            _Resolver(),
            localization_service=LocalizationService(),
        ).build_entry(
            forest,
            ITEMS,
            "item",
            overlay=overlay,
        )

        self.assertEqual(entry.name_text, "Имя проекта")
        self.assertEqual(entry.name_key, "st_name")
        self.assertEqual(entry.name_source.path, self.ru)


class ItemCardTextEditTest(unittest.TestCase):
    def _entry(self):
        return ItemEntry(
            section="item",
            fields=(
                ItemField("inv_name", "st_name", 2),
                ItemField("description", "st_desc", 3),
            ),
            name_text="Имя",
            description_text="Описание",
            name_source=LocalizationString(
                "st_name",
                "Имя",
                Path("ru.xml"),
            ),
            description_source=LocalizationString(
                "st_desc",
                "Описание",
                Path("ru.xml"),
            ),
        )

    def test_name_edit_calls_callback(self):
        calls = []
        card = ItemCardView(
            get_entry=self._entry,
            get_image=lambda section: None,
            can_edit=lambda: True,
            on_text_edit=lambda section, field, value: (
                calls.append((section, field, value)) or True
            ),
        )
        card.refresh()

        card.name_label.setText("Новое имя")
        card._apply_name_text()

        self.assertEqual(calls, [("item", "name", "Новое имя")])
        # После применения поле снова выглядит как часть карточки.
        self.assertTrue(card.name_label.isReadOnly())
        self.assertFalse(card.name_label.is_editing())

    def test_description_edit_calls_callback(self):
        calls = []
        card = ItemCardView(
            get_entry=self._entry,
            get_image=lambda section: None,
            can_edit=lambda: True,
            on_text_edit=lambda section, field, value: (
                calls.append((section, field, value)) or True
            ),
        )
        card.refresh()

        card.description_label.setPlainText("Новое описание")
        card._apply_description_text()

        self.assertEqual(
            calls,
            [("item", "description", "Новое описание")],
        )

    def test_description_renders_engine_text_and_edits_raw_xml(self):
        raw = (
            r"Строка\n"
            "\r\n\t\t"
            r"%c[255,0,0,255]Красный"
            r"%c[default] обычный"
        )
        entry = ItemEntry(
            section="item",
            fields=(),
            name_text="Имя",
            description_text="Строка\nКрасный обычный",
            name_source=LocalizationString(
                "st_name",
                "Имя",
                Path("ru.xml"),
            ),
            description_source=LocalizationString(
                "st_desc",
                "Строка\nКрасный обычный",
                Path("ru.xml"),
                raw_text=raw,
            ),
        )
        card = ItemCardView(
            get_entry=lambda: entry,
            get_image=lambda section: None,
            can_edit=lambda: True,
            on_text_edit=lambda *args: True,
        )
        card.refresh()

        # В обычном состоянии — как в движке: меток нет, есть перевод
        # строки и цвет.
        shown = card.description_label.toPlainText()
        self.assertEqual(shown, "Строка\nКрасный обычный")
        self.assertEqual(
            card.description_label.raw_text(),
            r"Строка\n%c[255,0,0,255]Красный%c[default] обычный",
        )

        block = card.description_label.document().begin().next()
        self.assertEqual(block.begin().fragment().charFormat()
                         .foreground().color().name(), "#0000ff")

        # В редакторе литералы ``\n`` превращены в настоящие переносы,
        # а ``%c[...]`` остаются видимым текстом. Реальных CR/TAB из
        # форматирования XML здесь уже нет.
        self.assertTrue(card.description_label.begin_editing())
        edited = card.description_label.toPlainText()
        self.assertIn("\n", edited)
        self.assertNotIn(r"\n", edited)
        self.assertIn("%c[255,0,0,255]", edited)
        self.assertIn("%c[default]", edited)
        self.assertNotIn("\r", edited)
        self.assertNotIn("\t", edited)
        self.assertEqual(
            card.description_label.raw_text(),
            r"Строка\n%c[255,0,0,255]Красный%c[default] обычный",
        )
        card.description_label.end_editing()
        self.assertEqual(
            card.description_label.toPlainText(),
            "Строка\nКрасный обычный",
        )

    def test_opening_editor_does_not_rewrite_formatting_controls(self):
        raw = r"Строка\n" + "\r\n\t\t" + r"%c[red]Красный"
        calls = []
        entry = ItemEntry(
            section="item",
            fields=(),
            name_text="Имя",
            description_text="Строка\nКрасный",
            description_source=LocalizationString(
                "st_desc",
                "Строка\nКрасный",
                Path("ru.xml"),
                raw_text=raw,
            ),
        )
        card = ItemCardView(
            get_entry=lambda: entry,
            get_image=lambda section: None,
            can_edit=lambda: True,
            on_text_edit=lambda *args: calls.append(args) or True,
        )
        card.refresh()

        card.description_label.begin_editing()
        card._apply_description_text()

        self.assertEqual(calls, [])

    def test_description_attaches_hint_engine(self):
        captured = {}

        def attach(widget, target=None):
            captured["widget"] = widget
            captured["target"] = target
            return object()

        entry = ItemEntry(
            section="item",
            fields=(),
            name_text="Имя",
            description_text="Текст",
            description_source=LocalizationString(
                "st_desc",
                "Текст",
                Path("ru.xml"),
                raw_text=r"Текст\n%c[ui",
            ),
        )
        card = ItemCardView(
            get_entry=lambda: entry,
            get_image=lambda section: None,
            can_edit=lambda: True,
            on_text_edit=lambda *args: True,
            color_defs={
                "ui_gray_3": (170, 170, 170, 255),
                "d_blue": (80, 120, 220, 255),
            },
            attach_hints=attach,
        )
        card.refresh()

        self.assertIs(captured.get("widget"), card.description_label)
        target = captured["target"]()
        self.assertEqual(target.surface, "engine_text")
        self.assertEqual(target.language, "localization_text")
        self.assertEqual(target.role, "item.description")

    def test_save_button_appears_only_while_editing(self):
        card = ItemCardView(
            get_entry=self._entry,
            get_image=lambda section: None,
            can_edit=lambda: True,
            on_text_edit=lambda *args: True,
        )
        card.refresh()

        self.assertTrue(card.description_save_btn.isHidden())
        self.assertTrue(card.description_label.begin_editing())
        self.assertFalse(card.description_save_btn.isHidden())
        card.description_label.end_editing()
        self.assertTrue(card.description_save_btn.isHidden())

    def test_save_button_applies_description(self):
        calls = []
        card = ItemCardView(
            get_entry=self._entry,
            get_image=lambda section: None,
            can_edit=lambda: True,
            on_text_edit=lambda section, field, value: (
                calls.append((section, field, value)) or True
            ),
        )
        card.refresh()

        card.description_label.begin_editing()
        card.description_label.setPlainText("Новое описание")
        card.description_save_btn.click()

        self.assertEqual(
            calls,
            [("item", "description", "Новое описание")],
        )

    def test_name_save_button_appears_only_while_editing(self):
        card = ItemCardView(
            get_entry=self._entry,
            get_image=lambda section: None,
            can_edit=lambda: True,
            on_text_edit=lambda *args: True,
        )
        card.refresh()

        self.assertTrue(card.name_save_btn.isHidden())
        self.assertTrue(card.name_label.begin_editing())
        self.assertFalse(card.name_save_btn.isHidden())
        card.name_label.end_editing()
        self.assertTrue(card.name_save_btn.isHidden())

    def test_name_save_button_applies_name(self):
        calls = []
        card = ItemCardView(
            get_entry=self._entry,
            get_image=lambda section: None,
            can_edit=lambda: True,
            on_text_edit=lambda section, field, value: (
                calls.append((section, field, value)) or True
            ),
        )
        card.refresh()

        card.name_label.begin_editing()
        card.name_label.setText("Новое имя")
        card.name_save_btn.click()

        self.assertEqual(calls, [("item", "name", "Новое имя")])

    def test_double_click_activates_text_editing(self):
        card = ItemCardView(
            get_entry=self._entry,
            get_image=lambda section: None,
            can_edit=lambda: True,
            on_text_edit=lambda *args: True,
        )
        card.refresh()
        card.show()
        _app.processEvents()

        position = QPoint(5, 5)
        QTest.mouseClick(
            card.name_label,
            Qt.LeftButton,
            Qt.NoModifier,
            position,
        )
        self.assertFalse(card.name_label.is_editing())

        QTest.mouseDClick(
            card.name_label,
            Qt.LeftButton,
            Qt.NoModifier,
            position,
        )
        self.assertTrue(card.name_label.is_editing())
        card.name_label.end_editing()

        viewport = card.description_label.viewport()
        QTest.mouseClick(
            viewport,
            Qt.LeftButton,
            Qt.NoModifier,
            position,
        )
        self.assertFalse(card.description_label.is_editing())

        QTest.mouseDClick(
            viewport,
            Qt.LeftButton,
            Qt.NoModifier,
            position,
        )
        self.assertTrue(card.description_label.is_editing())
        card.description_label.end_editing()
        card.close()

    def test_click_activates_edit_and_focus_out_returns_label_look(self):
        card = ItemCardView(
            get_entry=self._entry,
            get_image=lambda section: None,
            can_edit=lambda: True,
            on_text_edit=lambda *args: True,
        )
        card.refresh()

        self.assertTrue(card.name_label.isReadOnly())
        card.name_label.begin_editing()
        self.assertFalse(card.name_label.isReadOnly())
        card.name_label.end_editing()
        self.assertTrue(card.name_label.isReadOnly())

        self.assertTrue(card.description_label.isReadOnly())
        card.description_label.begin_editing()
        self.assertFalse(card.description_label.isReadOnly())
        card.description_label.end_editing()
        self.assertTrue(card.description_label.isReadOnly())

    def test_saving_blocks_input_and_shows_wait_cursor(self):
        card = ItemCardView(
            get_entry=self._entry,
            get_image=lambda section: None,
            can_edit=lambda: True,
            on_text_edit=lambda *args: True,
        )
        card.refresh()

        card.set_saving(True)
        self.assertFalse(card.name_label.isEnabled())
        self.assertFalse(card.description_label.isEnabled())
        self.assertFalse(card.fields_table.isEnabled())

        card.set_saving(False)
        self.assertTrue(card.name_label.isEnabled())
        self.assertTrue(card.description_label.isEnabled())
        self.assertTrue(card.fields_table.isEnabled())

    def test_edit_right_checked_when_editing_starts(self):
        state = {"active": False}
        card = ItemCardView(
            get_entry=self._entry,
            get_image=lambda section: None,
            can_edit=lambda: state["active"],
            on_text_edit=lambda *args: True,
        )
        card.refresh()

        self.assertTrue(card.name_label.isReadOnly())
        self.assertFalse(card.name_label.begin_editing())
        self.assertFalse(card.description_label.begin_editing())

        # Проект создан после открытия карточки: повторная отрисовка
        # не нужна, право проверяется в момент клика.
        state["active"] = True
        self.assertTrue(card.name_label.begin_editing())
        self.assertFalse(card.name_label.isReadOnly())
        self.assertTrue(card.description_label.begin_editing())
        self.assertFalse(card.description_label.isReadOnly())

    def test_field_table_edit_right_checked_when_editor_opens(self):
        state = {"active": False}
        card = ItemCardView(
            get_entry=self._entry,
            get_image=lambda section: None,
            can_edit=lambda: state["active"],
            on_replace_line=lambda *args: True,
        )
        card.refresh()

        index = card.fields_table.model().index(0, 1)
        card.fields_table.edit(index)
        self.assertIsNone(card.fields_table.findChild(QLineEdit))

        state["active"] = True
        card.fields_table.edit(index)
        self.assertIsNotNone(card.fields_table.findChild(QLineEdit))

    def test_without_source_text_is_read_only(self):
        entry = ItemEntry(
            section="item",
            fields=(ItemField("inv_name", "st_missing", 2),),
            name_text="st_missing",
        )
        card = ItemCardView(
            get_entry=lambda: entry,
            get_image=lambda section: None,
            can_edit=lambda: True,
            on_text_edit=lambda *args: True,
        )
        card.refresh()

        self.assertTrue(card.name_label.isReadOnly())


class _Ui:
    def __init__(self, entry, path):
        self.entry = entry
        self.path = path
        self.files = {
            path: RU_XML,
        }
        self.errors = []
        self.logs = []
        self.status = []
        self.opened_raw = []

    def open_raw_at_line(self, path, line_number):
        self.opened_raw.append((Path(path), int(line_number)))

    def open_raw(self, path):
        self.opened_raw.append((Path(path), 0))

    def is_project_active(self):
        return True

    def has_resolved_document(self, tab_id):
        return True

    def item_entry(self, tab_id, section):
        return self.entry

    def read_project_text(self, path):
        return self.files.get(Path(path), "")

    def write_project_text(self, path, text):
        self.files[Path(path)] = str(text)
        return True

    def show_error(self, message):
        self.errors.append(str(message))

    def log(self, message):
        self.logs.append(str(message))

    def set_status(self, message, timeout=0):
        self.status.append(str(message))


class _Services:
    def edit_localization_text(self, text, key, value):
        return edit_localization_text(text, key, value)


class _Ctx:
    def __init__(self, ui):
        self.ui = ui
        self.services = _Services()


class ItemTextFeatureTest(unittest.TestCase):
    def test_apply_writes_xml_draft_and_refreshes(self):
        path = Path("gamedata") / "configs" / "text" / "rus" / "ru.xml"
        entry = ItemEntry(
            section="item",
            fields=(),
            name_text="Старое имя",
            name_source=LocalizationString(
                "st_name",
                "Старое имя",
                path,
            ),
        )
        ui = _Ui(entry, path)
        feature = ItemViewerFeature(_Ctx(ui), session=None)

        self.assertTrue(
            feature._apply_item_text("tab", "item", "name", "Новое имя")
        )

        self.assertIn("<text>Новое имя</text>", ui.files[path])
        self.assertIn(
            "<text>Старое описание</text>",
            ui.files[path],
        )
        self.assertEqual(ui.errors, [])

    def test_apply_without_source_reports_error(self):
        entry = ItemEntry(section="item", fields=())
        ui = _Ui(entry, Path("ru.xml"))
        feature = ItemViewerFeature(_Ctx(ui), session=None)

        self.assertFalse(
            feature._apply_item_text("tab", "item", "name", "x")
        )
        self.assertTrue(ui.errors)


class _AsyncUi(_Ui):
    def __init__(self, entry, path):
        super().__init__(entry, path)
        self.async_calls = []
        self.pending = []
        self.busy = []
        self.statuses = []

    def write_project_text_async(
        self,
        path,
        text,
        on_done=None,
        original_text=None,
    ):
        self.async_calls.append(
            (Path(path), text, original_text)
        )
        self.pending.append((Path(path), text, on_done))
        return True

    def set_editor_tab_busy(self, tab_id, busy):
        self.busy.append((str(tab_id), bool(busy)))

    def set_status(self, message, timeout=0):
        self.statuses.append(str(message))


class TextSourceNavigationTest(unittest.TestCase):
    def test_feature_opens_raw_xml_at_source_line(self):
        path = Path("gamedata") / "configs" / "text" / "rus" / "ru.xml"
        source = LocalizationString("st_desc", "", path, line=17)
        ui = _Ui(ItemEntry(section="item", fields=()), path)
        feature = ItemViewerFeature(_Ctx(ui), session=None)

        self.assertTrue(feature._open_text_source(source))
        self.assertEqual(ui.opened_raw, [(path, 17)])

    def test_field_source_uses_text_sources_for_short_name(self):
        path = Path("ru.xml")
        short = LocalizationString("st_short", "", path, line=5)
        entry = ItemEntry(
            section="item",
            fields=(
                ItemField("inv_name_short", "st_short", 1),
                ItemField("kind", "i_food", 2),
            ),
            text_sources={"inv_name_short": short},
        )
        card = ItemCardView(
            get_entry=lambda: entry,
            get_image=lambda section: None,
            can_edit=lambda: False,
        )
        card.refresh()

        source = card._text_source_for_field(entry.fields[0])
        self.assertEqual(source.key, "st_short")
        self.assertIsNone(card._text_source_for_field(entry.fields[1]))


class _FakeCard:
    def __init__(self, section):
        self._section = section
        self.saving = []
        self.refreshed = []

    def section(self):
        return self._section

    def set_saving(self, saving):
        self.saving.append(bool(saving))

    def refresh(self, entry=None):
        self.refreshed.append(entry)

    def setReadOnly(self, _value):
        pass


class AsyncItemTextFeatureTest(unittest.TestCase):
    def _entry(self, path):
        return ItemEntry(
            section="item",
            fields=(),
            name_text="Старое имя",
            name_source=LocalizationString(
                "st_name",
                "Старое имя",
                path,
            ),
        )

    def test_edit_uses_async_writer_and_marks_card_busy(self):
        path = Path("gamedata") / "configs" / "text" / "rus" / "ru.xml"
        ui = _AsyncUi(self._entry(path), path)
        feature = ItemViewerFeature(_Ctx(ui), session=None)
        card = _FakeCard("item")
        feature._cards["card-tab"] = ("tab", "item", card)

        self.assertTrue(
            feature._apply_item_text("tab", "item", "name", "Новое имя")
        )

        self.assertEqual(len(ui.async_calls), 1)
        self.assertEqual(ui.async_calls[0][0], path)
        self.assertIn(("card-tab", True), ui.busy)
        self.assertEqual(card.saving, [True])

        # Запись ещё выполняется: повторный запуск для этого файла
        # запрещён, ввод остаётся заблокированным.
        self.assertFalse(
            feature._apply_item_text("tab", "item", "name", "Ещё имя")
        )
        self.assertEqual(len(ui.async_calls), 1)
        self.assertIn("уже сохраняется", ui.statuses[-1])

        _path, _text, on_done = ui.pending[0]
        on_done(True, None)

        self.assertEqual(card.saving, [True, False])
        self.assertIn(("card-tab", False), ui.busy)
        self.assertEqual(feature._text_save_jobs, {})

    def test_edit_is_rolled_back_when_save_fails(self):
        path = Path("gamedata") / "configs" / "text" / "rus" / "ru.xml"
        ui = _AsyncUi(self._entry(path), path)
        feature = ItemViewerFeature(_Ctx(ui), session=None)
        card = _FakeCard("item")
        feature._cards["card-tab"] = ("tab", "item", card)

        self.assertTrue(
            feature._apply_item_text("tab", "item", "name", "Новое имя")
        )
        _path, _text, on_done = ui.pending[0]
        on_done(False, "диск недоступен")

        self.assertTrue(ui.errors)
        self.assertEqual(card.saving, [True, False])
        self.assertEqual(feature._text_save_jobs, {})


if __name__ == "__main__":
    unittest.main()
