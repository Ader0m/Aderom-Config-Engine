# -*- coding: utf-8 -*-
"""Справочник sound_type из ai_sounds.h."""

import unittest

from PyQt5.QtCore import Qt

from tests.support import SRC  # noqa: F401
from tests.qt_support import qapp

from modules.ogg_editor.sound_types import (
    BASIC_SOUND_TYPES,
    EXTENDED_SOUND_TYPES,
    SOUND_TYPES,
    find_sound_type,
    sound_type_display,
)
from modules.ogg_editor import sound_types_ui as sound_types_ui_module
from modules.ogg_editor.sound_types_ui import (
    SoundTypeCombo,
    SoundTypeField,
    SoundTypeInfoDialog,
    SoundTypePicker,
    _info_text,
    show_sound_type_info,
)

_app = qapp()


class SoundTypeCatalogTest(unittest.TestCase):
    def test_catalog_has_known_engine_values(self):
        expected = {
            0x00000000: "undefined",
            0x80200000: "Выстрел",
            0x10000800: "Ожидание",
            0x08000080: "Окружение",
        }
        for value, label in expected.items():
            entry = find_sound_type(value)
            self.assertIsNotNone(entry, value)
            self.assertEqual(entry.label, label)

        self.assertEqual(len(SOUND_TYPES), 46)
        self.assertEqual(len({item.value for item in SOUND_TYPES}), 46)
        self.assertEqual(len(BASIC_SOUND_TYPES), 21)
        self.assertEqual(len(EXTENDED_SOUND_TYPES), 25)

    def test_unknown_value_is_not_hidden(self):
        value = 0x12345678
        self.assertIsNone(find_sound_type(value))
        self.assertIn("Неизвестный", sound_type_display(value))
        self.assertIn("12345678", sound_type_display(value))

    def test_combo_keeps_exact_value(self):
        combo = SoundTypeCombo()
        combo.set_value(0x80200000)
        self.assertEqual(combo.value(), 0x80200000)

        combo.set_value(0x80000000)
        self.assertEqual(combo.value(), 0x80000000)
        self.assertIn("вне 21", combo.currentText())

        combo.set_value(0x12345678)
        self.assertEqual(combo.value(), 0x12345678)
        self.assertIn(
            "12345678",
            combo.currentText().lower(),
        )

        combo.set_value(0x08000400)
        self.assertEqual(combo.value(), 0x08000400)
        self.assertNotIn("12345678", combo.currentText().lower())

        hint = combo.view().sizeHintForColumn(0)
        self.assertGreaterEqual(combo._popup_width(), hint)

    def test_picker_has_only_canonical_types(self):
        picker = SoundTypePicker(current_value=0x08000080)
        try:
            self.assertEqual(picker.plus_button.text(), "?")
            self.assertEqual(picker.listing.count(), 21)
            values = {
                picker.listing.item(row).data(Qt.UserRole)
                for row in range(picker.listing.count())
            }
            self.assertIn(0x00000000, values)
            self.assertIn(0x80200000, values)
            self.assertIn(0x08000080, values)
            self.assertNotIn(0x80000000, values)
            self.assertNotIn(0x00200000, values)
            self.assertTrue(picker.current_label.isHidden())
        finally:
            picker.close()

    def test_picker_explains_non_basic_current_value(self):
        picker = SoundTypePicker(current_value=0x80000000)
        try:
            self.assertFalse(picker.current_label.isHidden())
            self.assertIn("Маска: Оружие", picker.current_label.text())
            self.assertIn("вне 21", picker.current_label.text())
        finally:
            picker.close()

    def test_field_uses_canonical_list(self):
        field = SoundTypeField()
        self.assertEqual(field.plus_button.text(), "?")
        self.assertEqual(field.basic.count(), 21)

        field.set_value(0x80200000)
        self.assertEqual(field.value(), 0x80200000)

        field.set_value(0x08000080)
        self.assertEqual(field.value(), 0x08000080)

    def test_info_dialog_is_read_only_and_lists_bits(self):
        dialog = SoundTypeInfoDialog()
        try:
            self.assertTrue(dialog.text.isReadOnly())
            text = dialog.text.toPlainText()
            self.assertIn("Маски (5)", text)
            self.assertIn("Отдельные биты (20)", text)
            for entry in EXTENDED_SOUND_TYPES:
                self.assertIn(entry.display, text)
            self.assertIn("0x80200000", _info_text())
        finally:
            dialog.close()

    def test_info_window_is_modeless_and_reused(self):
        sound_types_ui_module._INFO_DIALOG = None
        show_sound_type_info()
        first = sound_types_ui_module._INFO_DIALOG
        try:
            self.assertIsNotNone(first)
            self.assertTrue(first.isVisible())
            self.assertFalse(first.isModal())

            show_sound_type_info()
            self.assertIs(sound_types_ui_module._INFO_DIALOG, first)
        finally:
            first.close()
            _app.processEvents()
        self.assertIsNone(sound_types_ui_module._INFO_DIALOG)


if __name__ == "__main__":
    unittest.main()
