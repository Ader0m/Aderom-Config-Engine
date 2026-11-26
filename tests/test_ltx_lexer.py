# -*- coding: utf-8 -*-
import unittest

from tests.support import SRC  # noqa: F401

from PyQt5.Qsci import QsciScintilla

from tests.qt_support import qapp
from ui import lexers


def styles(line):
    return [(start, end, style) for start, end, style in lexers.tokenize_line(line)]


class LtxLexerRestyleTest(unittest.TestCase):
    """Правка фрагмента строки должна перекрашивать строку целиком."""

    @classmethod
    def setUpClass(cls):
        cls.app = qapp()

    def test_incremental_recolor_starts_at_line_boundary(self):
        editor = QsciScintilla()
        self.addCleanup(editor.deleteLater)
        lexer = lexers.LtxLexer(editor)
        editor.setLexer(lexer)

        text = "allow_repair = false\n"
        editor.setText(text)
        editor.SendScintilla(QsciScintilla.SCI_COLOURISE, 0, -1)

        editor.setSelection(0, 15, 0, 20)
        editor.replaceSelectedText("12345")
        text = editor.text()
        changed = text.index("12345")

        # Scintilla запрашивает только изменённый фрагмент, начиная с
        # середины строки. Лексер обязан разобрать строку с её начала.
        lexer.styleText(changed, len(text))

        start = int(
            editor.SendScintilla(
                QsciScintilla.SCI_POSITIONFROMLINE,
                0,
            )
        )
        result = [
            int(
                editor.SendScintilla(
                    QsciScintilla.SCI_GETSTYLEAT,
                    start + index,
                )
            )
            for index in range(len(text.rstrip("\n")))
        ]
        self.assertEqual(
            result[changed:changed + 5],
            [lexers.STYLE_NUMBER] * 5,
        )
        self.assertEqual(
            result[:12],
            [lexers.STYLE_KEY] * 12,
        )


class LtxLexerTest(unittest.TestCase):
    def test_comments(self):
        self.assertEqual(styles("; comment"), [(0, 9, lexers.STYLE_COMMENT)])
        self.assertEqual(styles("  // comment"), [(2, 12, lexers.STYLE_COMMENT)])

    def test_section_kinds(self):
        self.assertEqual(styles("[sec]")[0][2], lexers.STYLE_SECTION)
        self.assertEqual(styles("![sec]")[0][2], lexers.STYLE_SECTION_OVERRIDE)
        self.assertEqual(styles("!![sec]")[0][2], lexers.STYLE_SECTION_DELETE)
        self.assertEqual(styles("@[sec]")[0][2], lexers.STYLE_SECTION_SAFE)

    def test_parents_and_removed_parent(self):
        tokens = styles("[sec]:base, !old")
        self.assertIn((6, 10, lexers.STYLE_PARENT), tokens)
        self.assertIn((12, 13, lexers.STYLE_PARENT_DELETE), tokens)
        self.assertIn((13, 16, lexers.STYLE_PARENT), tokens)

    def test_key_operators(self):
        self.assertEqual(styles("key = value")[0][2], lexers.STYLE_KEY)
        self.assertEqual(styles("!key")[0][2], lexers.STYLE_KEY_DELETE)
        self.assertEqual(styles(">key = value")[0][2], lexers.STYLE_KEY_INSERT)
        self.assertEqual(styles("<key = value")[0][2], lexers.STYLE_KEY_REMOVE)

    def test_values(self):
        tokens = styles("key = 123, 1.5, true, word")
        self.assertIn((6, 9, lexers.STYLE_NUMBER), tokens)
        self.assertIn((11, 14, lexers.STYLE_NUMBER), tokens)
        self.assertIn((16, 20, lexers.STYLE_CONSTANT), tokens)
        self.assertIn((22, 26, lexers.STYLE_LIST_VALUE), tokens)

    def test_comma_separated_list(self):
        tokens = styles(
            "bones = bip01_r_calf,bip01_l_calf,bip01_r_forearm"
        )
        values = [
            (start, end, style)
            for start, end, style in tokens
            if style == lexers.STYLE_LIST_VALUE
        ]
        self.assertEqual(
            values,
            [
                (8, 20, lexers.STYLE_LIST_VALUE),
                (21, 33, lexers.STYLE_LIST_VALUE),
                (34, 49, lexers.STYLE_LIST_VALUE),
            ],
        )
        self.assertTrue(
            any(style == lexers.STYLE_ASSIGN for _, _, style in tokens)
        )

    def test_include(self):
        tokens = styles('#include "configs/defines.ltx"')
        self.assertIn((0, 8, lexers.STYLE_DIRECTIVE), tokens)
        self.assertIn((9, 30, lexers.STYLE_INCLUDE_PATH), tokens)
        self.assertEqual(
            lexers._style_color(lexers.STYLE_DIRECTIVE),
            lexers._palette().syn_include,
        )

    def test_comment_inside_quotes_is_not_comment(self):
        tokens = styles('key = "a;//b"')
        self.assertFalse(any(style == lexers.STYLE_COMMENT for _, _, style in tokens))


if __name__ == "__main__":
    unittest.main()
