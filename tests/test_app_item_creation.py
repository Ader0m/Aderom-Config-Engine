# -*- coding: utf-8 -*-
"""Доменные правила создания предметов в app/features/items."""

import unittest

from tests.support import SRC  # noqa: F401

from app.features.items.creation import plan_item_creation, project_tag


class ItemCreationPlanTest(unittest.TestCase):
    def test_plan_builds_section_and_localization(self):
        plan = plan_item_creation(
            "",
            "",
            (),
            "new_item",
            "base_item",
            ("kind", "inv_name", "description"),
            "Мой проект",
        )

        self.assertEqual(plan.section, "new_item")
        self.assertEqual(plan.tag, "мой_проект")
        self.assertIn("[new_item]:base_item", plan.item_text)
        self.assertIn("kind =", plan.item_text)
        self.assertIn("st_ace_мой_проект_name", plan.item_text)
        self.assertIn("<string_table>", plan.xml_text)
        self.assertIn(plan.name_key, plan.xml_text)
        self.assertIn(plan.description_key, plan.xml_text)

    def test_existing_section_is_rejected(self):
        with self.assertRaises(ValueError):
            plan_item_creation(
                "",
                "",
                ("New_Item",),
                "new_item",
                None,
                (),
                "project",
            )

    def test_invalid_section_name_is_rejected(self):
        with self.assertRaises(ValueError):
            plan_item_creation(
                "",
                "",
                (),
                "bad[name]",
                None,
                (),
                "project",
            )

    def test_existing_localization_keys_get_next_suffix(self):
        xml_text = (
            '<string_table>\n'
            '<string id="st_ace_project_name"><text></text></string>\n'
            '<string id="st_ace_project_descr"><text></text></string>\n'
            "</string_table>\n"
        )
        plan = plan_item_creation(
            "",
            xml_text,
            (),
            "new_item",
            None,
            (),
            "project",
        )

        self.assertEqual(plan.name_key, "st_ace_project_2_name")
        self.assertEqual(plan.description_key, "st_ace_project_2_descr")

    def test_project_tag_is_safe(self):
        self.assertEqual(project_tag("Мой проект!"), "мой_проект")
        self.assertEqual(project_tag(""), "project")


if __name__ == "__main__":
    unittest.main()
