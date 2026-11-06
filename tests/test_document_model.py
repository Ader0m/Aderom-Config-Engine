# -*- coding: utf-8 -*-
import unittest

from tests.support import SRC  # noqa: F401

from types import SimpleNamespace

from core.documents.document_diff import diff_texts
from core.documents.document_model import parse_ltx_document
from core.documents.resolved_edit import analyze_resolved_changes


class DocumentModelTest(unittest.TestCase):
    def test_parse_sections_inheritance_and_operations(self):
        text = """
; comment
[base]:p1,p2
x = 1 ; tail
y = "a ; b"
z =
w
!del = old
>list = a
<list = b

[child]:base
k = v
"""
        doc = parse_ltx_document(text)

        self.assertEqual(set(doc.sections), {"base", "child"})
        self.assertEqual(doc.parents("child"), ("base",))

        view = doc.section_view("base")
        self.assertEqual(view.present["x"], "1")
        self.assertEqual(view.present["y"], '"a ; b"')
        self.assertIsNone(view.present["z"])
        self.assertIsNone(view.present["w"])
        self.assertIn("del", view.deleted)
        self.assertEqual(
            view.list_ops["list"],
            (("append", "a"), ("remove", "b")),
        )

    def test_multiline_value(self):
        text = '[sec]\ntext = "first\nsecond"\n'
        doc = parse_ltx_document(text)
        self.assertEqual(doc.values("sec")["text"], '"first\nsecond"')

    def test_delete_section(self):
        doc = parse_ltx_document("[a]\nx = 1\n!![b]\ny = 2\n")
        self.assertEqual(doc.deleted_sections, {"b"})
        self.assertNotIn("b", doc.sections)
        self.assertIsNone(doc.section("b"))
        self.assertEqual(doc.section_view("b").present, {})

    def test_section_header_kinds(self):
        doc = parse_ltx_document(
            "[base]\n![override]\n@[safe]\n!![deleted]\n"
        )
        self.assertEqual(doc.section("base").header_kind, "base")
        self.assertEqual(doc.section("override").header_kind, "override")
        self.assertEqual(doc.section("safe").header_kind, "safe")
        self.assertNotIn("deleted", doc.sections)
        self.assertIn("deleted", doc.deleted_sections)

    def test_section_names_are_case_insensitive_and_duplicate_keys_last_win(self):
        doc = parse_ltx_document(
            "[A]\nx = 1\n[a]\nx = 2\ny = 3\n"
        )
        self.assertEqual(set(doc.sections), {"a"})
        self.assertEqual(doc.values("a"), {"x": "2", "y": "3"})
        self.assertTrue(doc.diagnostics)

    def test_removed_parent_is_not_effective(self):
        doc = parse_ltx_document("[a]:p, !q\nx = 1\n")
        self.assertEqual(doc.parents("a"), ("p",))
        self.assertEqual(
            doc.section("a").effective_parents(),
            ("p",),
        )


class DocumentDiffTest(unittest.TestCase):
    def test_set_add_delete_key(self):
        diff = diff_texts(
            "[a]\nx = 1\ny = 2\n",
            "[a]\nx = 3\nz = 4\n",
        )
        kinds = {(c.kind, c.key) for c in diff.changes}
        self.assertIn(("set_key", "x"), kinds)
        self.assertIn(("delete_key", "y"), kinds)
        self.assertIn(("add_key", "z"), kinds)

    def test_no_changes(self):
        diff = diff_texts("[a]\nx = 1\n", "[a]\nx = 1\n")
        self.assertTrue(diff.is_empty)

    def test_parent_changes(self):
        diff = diff_texts(
            "[a]:p1\nx = 1\n",
            "[a]:p2\nx = 1\n",
        )
        kinds = {(c.kind, c.parent) for c in diff.changes}
        self.assertIn(("remove_parent", "p1"), kinds)
        self.assertIn(("add_parent", "p2"), kinds)

    def test_include_change_is_blocking(self):
        diff = diff_texts(
            '#include "a.ltx"\n[a]\nx = 1\n',
            '#include "b.ltx"\n[a]\nx = 1\n',
        )
        self.assertTrue(diff.has_blocking_issues)
        self.assertEqual(diff.blocking_issues()[0].code, "include_changed")

    def test_removing_list_operation_is_blocking(self):
        diff = diff_texts(
            "[a]\n>list = x\n",
            "[a]\n",
        )
        self.assertTrue(diff.has_blocking_issues)
        self.assertEqual(
            diff.blocking_issues()[0].code,
            "list_operation_removed",
        )


class ResolvedEditTest(unittest.TestCase):
    def test_value_change_maps_section_key_and_winner(self):
        source = SimpleNamespace(display="MO2\\Weapons\\a.ltx", path=None)
        analysis = analyze_resolved_changes(
            "[a]\nx = 1\ny = 2\n",
            "[a]\nx = 3\ny = 2\n",
            [None, source, source],
        )

        self.assertTrue(analysis.is_supported)
        self.assertEqual(len(analysis.changes), 1)
        change = analysis.changes[0]
        self.assertEqual(change.section, "a")
        self.assertEqual(change.key, "x")
        self.assertEqual(change.winner_label, "MO2\\Weapons\\a.ltx")

    def test_section_header_change_is_unsupported(self):
        analysis = analyze_resolved_changes(
            "[a]\nx = 1\n",
            "[b]\nx = 1\n",
            [None, None],
        )
        self.assertFalse(analysis.is_supported)
        self.assertEqual(analysis.unsupported[0].kind, "structural")

    def test_line_insertion_is_unsupported(self):
        analysis = analyze_resolved_changes(
            "[a]\nx = 1\n",
            "[a]\nx = 1\ny = 2\n",
            [None, None],
        )
        self.assertFalse(analysis.is_supported)
        self.assertEqual(analysis.unsupported[0].kind, "unsupported")


if __name__ == "__main__":
    unittest.main()
