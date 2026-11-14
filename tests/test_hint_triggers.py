# -*- coding: utf-8 -*-
"""Чистые детекторы контекста подсказок."""

import unittest

from tests.support import SRC  # noqa: F401

from ui.hints import triggers


class HintTriggersTest(unittest.TestCase):
    def test_color(self):
        trigger = triggers.color_trigger("%c[ui")
        self.assertIsNotNone(trigger)
        self.assertEqual(trigger.content_start, 3)
        self.assertEqual(trigger.prefix, "ui")
        self.assertIsNone(triggers.color_trigger("%c[ui]"))
        self.assertIsNotNone(triggers.color_trigger("text %C[gray"))

    def test_section_headers(self):
        trigger = triggers.section_trigger("  [sec")
        self.assertEqual(trigger.name_start, 3)
        self.assertEqual(trigger.prefix, "sec")
        self.assertEqual(trigger.operator, "")

        trigger = triggers.section_trigger("![sec")
        self.assertEqual(trigger.name_start, 2)
        self.assertEqual(trigger.operator, "!")

        trigger = triggers.section_trigger("!![sec")
        self.assertEqual(trigger.name_start, 3)
        self.assertEqual(trigger.operator, "!!")

        trigger = triggers.section_trigger("@[sec")
        self.assertEqual(trigger.name_start, 2)
        self.assertEqual(trigger.operator, "@")

        self.assertIsNone(triggers.section_trigger("[sec]"))

    def test_parents(self):
        trigger = triggers.parent_trigger("[sec]:pa")
        self.assertEqual(trigger.prefix, "pa")
        self.assertEqual(trigger.exclude, ())

        trigger = triggers.parent_trigger("[sec]:base, !ol")
        self.assertEqual(trigger.prefix, "ol")
        self.assertEqual(trigger.operator, "!")
        self.assertEqual(trigger.exclude, ("base",))
        self.assertIsNone(triggers.parent_trigger("[sec]"))

    def test_keys(self):
        trigger = triggers.key_trigger("  inv")
        self.assertEqual(trigger.prefix, "inv")
        self.assertEqual(trigger.name_start, 2)
        self.assertEqual(trigger.operator, "")

        trigger = triggers.key_trigger("  !key")
        self.assertEqual(trigger.prefix, "key")
        self.assertEqual(trigger.operator, "!")
        self.assertEqual(trigger.name_start, 3)

        self.assertIsNone(triggers.key_trigger("key = value"))
        self.assertIsNone(triggers.key_trigger("[sec"))
        self.assertIsNone(triggers.key_trigger(""))
        self.assertIsNone(triggers.key_trigger("; comment"))

    def test_include(self):
        trigger = triggers.include_trigger('#include "con')
        self.assertTrue(trigger.quoted)
        self.assertEqual(trigger.prefix, "con")

        trigger = triggers.include_trigger("#include con")
        self.assertFalse(trigger.quoted)
        self.assertEqual(trigger.prefix, "con")

        self.assertIsNone(triggers.include_trigger("#include"))
        self.assertIsNone(triggers.include_trigger('#include "con"'))

    def test_directive(self):
        trigger = triggers.directive_trigger("#inc")
        self.assertEqual(trigger.prefix, "inc")
        self.assertIsNone(triggers.directive_trigger("#include"))
        self.assertIsNone(triggers.directive_trigger("text #inc"))

    def test_localization(self):
        trigger = triggers.localization_trigger("inv_name = st", "")
        self.assertEqual(trigger.key, "inv_name")
        self.assertEqual(trigger.prefix, "st")
        self.assertFalse(trigger.quoted)

        trigger = triggers.localization_trigger(
            'description = "st',
            'm"',
        )
        self.assertTrue(trigger.quoted)
        self.assertEqual(trigger.replace_after, 1)

        self.assertIsNone(triggers.localization_trigger("cost = st", ""))
        self.assertIsNone(triggers.localization_trigger("inv_name = st ", ""))


if __name__ == "__main__":
    unittest.main()
