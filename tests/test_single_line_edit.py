# -*- coding: utf-8 -*-
import unittest

from tests.support import SRC  # noqa: F401

from core.single_line_edit import analyze_single_line_edit


class SingleLineEditTest(unittest.TestCase):
    def test_value_change(self):
        analysis = analyze_single_line_edit(
            "[a]\nx = 1\n",
            "[a]\nx = 2\n",
        )
        self.assertTrue(analysis.is_supported)
        self.assertEqual(len(analysis.changes), 1)
        self.assertEqual(analysis.changes[0].kind, "set_key")
        self.assertEqual(analysis.changes[0].key, "x")
        self.assertEqual(analysis.changes[0].new_value, "2")

    def test_key_rename_becomes_delete_plus_add(self):
        analysis = analyze_single_line_edit(
            "[a]\nx = 1\n",
            "[a]\ny = 1\n",
        )
        self.assertTrue(analysis.is_supported)
        kinds = {change.kind for change in analysis.changes}
        self.assertEqual(kinds, {"delete_key", "add_key"})
        self.assertTrue(any(change.risk == "confirm" for change in analysis.changes))

    def test_key_delete_by_blank_line(self):
        analysis = analyze_single_line_edit(
            "[a]\nx = 1\n",
            "[a]\n\n",
        )
        self.assertTrue(analysis.is_supported)
        self.assertEqual(analysis.changes[0].kind, "delete_key")

    def test_key_add_by_blank_line(self):
        analysis = analyze_single_line_edit(
            "[a]\n\n",
            "[a]\nx = 1\n",
        )
        self.assertTrue(analysis.is_supported)
        self.assertEqual(analysis.changes[0].kind, "add_key")

    def test_list_append_from_blank_line(self):
        analysis = analyze_single_line_edit(
            "[a]\nlist = a,b\n\n",
            "[a]\nlist = a,b\n>list = c\n",
        )
        self.assertTrue(analysis.is_supported)
        self.assertEqual(analysis.changes[0].kind, "list_append")
        self.assertEqual(analysis.changes[0].key, "list")

    def test_value_to_list_operation_is_blocked(self):
        analysis = analyze_single_line_edit(
            "[a]\nlist = a,b\n",
            "[a]\n>list = c\n",
        )
        self.assertTrue(analysis.has_errors)
        self.assertEqual(
            analysis.issues[0].code,
            "value_to_list_operation",
        )

    def test_parent_added(self):
        analysis = analyze_single_line_edit(
            "[a]\nx = 1\n",
            "[a]:base\nx = 1\n",
        )
        self.assertTrue(analysis.is_supported)
        self.assertEqual(analysis.changes[0].kind, "add_parent")
        self.assertEqual(analysis.changes[0].parent, "base")

    def test_section_deleted(self):
        analysis = analyze_single_line_edit(
            "[a]\nx = 1\n",
            "!![a]\nx = 1\n",
        )
        self.assertTrue(analysis.is_supported)
        self.assertEqual(analysis.changes[0].kind, "delete_section")

    def test_empty_section_at_end(self):
        analysis = analyze_single_line_edit(
            "[a]\nx = 1\n\n",
            "[a]\nx = 1\n[new]\n",
        )
        self.assertTrue(analysis.is_supported)
        self.assertEqual(analysis.changes[0].kind, "add_section")
        self.assertEqual(analysis.changes[0].section, "new")

    def test_multiple_lines_changed_is_blocked(self):
        analysis = analyze_single_line_edit(
            "[a]\nx = 1\ny = 2\n",
            "[a]\nx = 3\ny = 4\n",
        )
        self.assertTrue(analysis.has_errors)
        self.assertEqual(
            analysis.issues[0].code,
            "multiple_lines_changed",
        )

    def test_section_rename_is_blocked(self):
        analysis = analyze_single_line_edit(
            "[a]\nx = 1\n",
            "[b]\nx = 1\n",
        )
        self.assertTrue(analysis.has_errors)
        self.assertEqual(analysis.issues[0].code, "section_renamed")

    def test_line_insertion_is_blocked(self):
        analysis = analyze_single_line_edit(
            "[a]\nx = 1\n",
            "[a]\nx = 1\ny = 2\n",
        )
        self.assertTrue(analysis.has_errors)
        self.assertEqual(analysis.issues[0].code, "line_count_changed")

    def test_section_insertion_before_keys_is_blocked(self):
        analysis = analyze_single_line_edit(
            "[a]\n\nx = 1\n",
            "[a]\n[new]\nx = 1\n",
        )
        self.assertTrue(analysis.has_errors)
        self.assertEqual(
            analysis.issues[0].code,
            "section_insertion_structural",
        )


if __name__ == "__main__":
    unittest.main()
