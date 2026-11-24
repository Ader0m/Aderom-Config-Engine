# -*- coding: utf-8 -*-
"""LTX-контекст для подсказок через app-порт."""

import unittest

from tests.support import SRC  # noqa: F401

from app.hints import build_ltx_line_info


class LtxLineInfoTest(unittest.TestCase):
    @staticmethod
    def _lines(values):
        return lambda index: (
            values[index] if 0 <= index < len(values) else ""
        )

    def test_section_key_and_local_data(self):
        lines = ["[sec]\n", "key = value\n", "other = 1\n"]

        info = build_ltx_line_info(self._lines(lines), 2, 3)

        self.assertEqual(info.section, "sec")
        self.assertEqual(info.key, "other")
        self.assertEqual(info.value_kind, "bare")
        self.assertEqual(info.local_sections, ("sec",))
        self.assertEqual(info.local_keys, (("key", "value"),))
        self.assertFalse(info.in_comment)
        self.assertFalse(info.in_string)

    def test_header_line_and_comment(self):
        lines = ["[sec]\n", "; key = value\n"]

        header = build_ltx_line_info(self._lines(lines), 0, 0)
        self.assertEqual(header.section, "sec")
        self.assertEqual(header.key, "")
        self.assertEqual(header.value_kind, "")

        comment = build_ltx_line_info(self._lines(lines), 1, 0)
        self.assertTrue(comment.in_comment)


if __name__ == "__main__":
    unittest.main()
