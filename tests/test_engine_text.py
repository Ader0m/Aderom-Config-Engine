# -*- coding: utf-8 -*-
"""Разбор текста UI X-Ray (CUILines) для карточки предмета."""

import unittest

from tests.support import SRC  # noqa: F401

from sdk.engine_text import (
    engine_source_text,
    engine_text_lines,
    parse_color_definitions,
    parse_engine_text,
)


class ColorDefinitionsTest(unittest.TestCase):
    def test_named_colors_and_alpha(self):
        colors = parse_color_definitions(
            '<colors>'
            '<color name="ui_gray_3" r="170" g="171" b="172" a="200"/>'
            "<color name='d_blue' r='1' g='2' b='3'/>"
            "</colors>"
        )

        self.assertEqual(colors["ui_gray_3"], (170, 171, 172, 200))
        self.assertEqual(colors["d_blue"], (1, 2, 3, 255))


class EngineTextTest(unittest.TestCase):
    def test_numeric_tag_is_argb(self):
        runs = parse_engine_text("%c[255,10,20,30]X")

        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].text, "X")
        self.assertEqual(runs[0].color, (10, 20, 30, 255))

    def test_named_and_default_tags(self):
        runs = parse_engine_text(
            "%c[ui_gray_3]A%c[default]B",
            {"ui_gray_3": (170, 171, 172, 255)},
            "#010203",
        )

        self.assertEqual(runs[0].color, (170, 171, 172, 255))
        self.assertEqual(runs[1].color, (1, 2, 3, 255))

    def test_unknown_tag_falls_back_to_control_color(self):
        runs = parse_engine_text("%c[missing]A", {}, "#010203")

        self.assertEqual(runs[0].color, (1, 2, 3, 255))

    def test_literal_newline_is_line_break(self):
        self.assertEqual(
            engine_text_lines(r"A\n%c[255,0,0,255]B"),
            ("A", "B"),
        )

    def test_real_crlf_and_tabs_are_zero_width(self):
        # Как в шрифтовом атласе движка: у 0x09/0x0A/0x0D нулевая
        # ширина. Строку делит только литерал ``\n``.
        self.assertEqual(
            engine_text_lines("A\r\n\tB\\nC"),
            ("AB", "C"),
        )

    def test_tag_without_closing_bracket_stays_text(self):
        runs = parse_engine_text("%c[255,0,0")

        self.assertEqual(runs[0].text, "%c[255,0,0")

    def test_source_text_drops_xml_formatting_controls(self):
        self.assertEqual(
            engine_source_text("A\r\n\t\tB\\n%c[red]C"),
            r"AB\n%c[red]C",
        )


if __name__ == "__main__":
    unittest.main()
