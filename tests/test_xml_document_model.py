# -*- coding: utf-8 -*-
import unittest

from tests.support import SRC  # noqa: F401

from core.documents.xml_diff import (
    apply_xml_changes,
    diff_xml_documents,
    diff_xml_texts,
)
from core.documents.xml_model import (
    parse_xml_document,
    selector_segments,
    serialize_xml_document,
)


BEFORE = """<?xml version="1.0" encoding="windows-1251"?>
<root>
    <item id="a" width="10">old</item>
    <item id="b" width="20">keep</item>
    <group name="g">
        <child x="1"/>
    </group>
</root>
"""

AFTER = """<?xml version="1.0" encoding="windows-1251"?>
<root>
    <item id="a" width="12">new</item>
    <item id="b" width="20">keep</item>
    <item id="c" width="30">added</item>
    <group name="g">
        <child x="1"/>
        <child x="2"/>
    </group>
</root>
"""


class XmlDocumentModelTest(unittest.TestCase):
    def test_parse_and_roundtrip(self):
        document = parse_xml_document(BEFORE, source_path="ui/test.xml")
        self.assertEqual([node.tag for node in document.roots()], ["root"])
        self.assertEqual(document.element_count(), 5)

        text = serialize_xml_document(document)
        parsed = parse_xml_document(text)
        self.assertEqual(document.canonical(), parsed.canonical())

    def test_attributes_and_text(self):
        document = parse_xml_document(BEFORE)
        item = document.find((("root", 0), ("item", 0)))
        self.assertIsNotNone(item)
        self.assertEqual(item.attrs["id"], "a")
        self.assertEqual(item.attrs["width"], "10")
        self.assertEqual(item.text(), "old")

    def test_includes_and_fragment(self):
        document = parse_xml_document(
            '<?xml version="1.0" encoding="windows-1251"?>\n'
            '#include "a.xml"\n'
            "#include \"b.xml\"\n"
            "<root/>\n"
        )
        self.assertEqual(len(document.includes), 2)
        self.assertEqual(document.roots()[0].tag, "root")

        fragment = parse_xml_document("<a/><b/>")
        self.assertTrue(fragment.fragment)
        self.assertEqual([node.tag for node in fragment.roots()], ["a", "b"])

    def test_lenient_fallback(self):
        document = parse_xml_document(
            "<root><text>a &amp; b & c</text></root>"
        )
        root = document.roots()[0]
        text = root.element_children()[0]
        self.assertEqual(text.text(), "a & b & c")
        self.assertTrue(document.diagnostics)

    def test_selectors(self):
        document = parse_xml_document(BEFORE)
        root = document.find((("root", 0),))
        first = document.find((("root", 0), ("item", 0)))
        second_child = document.find(
            (("root", 0), ("group", 0), ("child", 0))
        )
        self.assertEqual(selector_segments(document, root), ("", 1, True))
        self.assertEqual(
            selector_segments(document, first),
            ("item[id=a]", 1, False),
        )
        self.assertEqual(
            selector_segments(document, second_child),
            ("group[name=g] > child", 1, False),
        )


class XmlDiffTest(unittest.TestCase):
    def test_diff_and_apply(self):
        before = parse_xml_document(BEFORE)
        after = parse_xml_document(AFTER)
        diff = diff_xml_documents(before, after)

        kinds = [change.kind for change in diff.changes]
        self.assertIn("xml_set_attr", kinds)
        self.assertIn("xml_set_text", kinds)
        self.assertEqual(kinds.count("xml_add_element"), 2)
        self.assertFalse(diff.has_blocking_issues)

        applied = apply_xml_changes(before, diff.changes)
        self.assertEqual(applied.canonical(), after.canonical())

    def test_attribute_and_element_removal(self):
        after = """<root>
            <item id="b">keep</item>
            <group name="g"><child x="1"/></group>
        </root>
        """
        diff = diff_xml_texts(BEFORE, after)
        kinds = [change.kind for change in diff.changes]
        self.assertIn("xml_delete_element", kinds)
        self.assertIn("xml_delete_attr", kinds)
        applied = apply_xml_changes(diff.before, diff.changes)
        self.assertEqual(applied.canonical(), diff.after.canonical())

    def test_reorder_is_blocked(self):
        diff = diff_xml_texts(
            '<root><a id="1"/><b id="2"/></root>',
            '<root><b id="2"/><a id="1"/></root>',
        )
        self.assertTrue(diff.has_blocking_issues)
        self.assertEqual(
            diff.blocking_issues()[0].code,
            "xml_reorder_unsupported",
        )

    def test_include_change_is_blocked(self):
        diff = diff_xml_texts(
            '<root><a/></root>',
            '#include "x.xml"\n<root><a/></root>',
        )
        self.assertTrue(diff.has_blocking_issues)
        self.assertEqual(diff.blocking_issues()[0].code, "include_changed")


if __name__ == "__main__":
    unittest.main()
