# -*- coding: utf-8 -*-
import pickle
import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.resolver.ltx import LtxResolverAdapter


class _Item:
    def __init__(self, first, second, filename):
        self.first = first
        self.second = second
        self.filename = filename


class _Section:
    def __init__(self, data):
        self.data = data


class _Resolver:
    def __init__(self, root, **_kwargs):
        self.root = root
        self.data = []

    def load(self):
        # Один и тот же filename в нескольких секциях.
        self.data = [
            ("s1", _Section([_Item("a", "1", "defines")])),
            ("s2", _Section([_Item("b", "2", "defines")])),
            ("s3", _Section([_Item("c", "3", "defines")])),
        ]


class _LineItem(_Item):
    def __init__(self, first, second, filename, line):
        super().__init__(first, second, filename)
        self.line = line


class _LineResolver(_Resolver):
    def load(self):
        self.data = [
            ("s1", _Section([
                _LineItem("a", "1", "defines", 2),
                _LineItem("b", "2", "defines", 7),
            ])),
        ]


class ResolverAdapterTest(unittest.TestCase):
    def _adapter(self):
        adapter = LtxResolverAdapter.__new__(LtxResolverAdapter)
        adapter.resolver_script = Path("Resolver4.py")
        adapter.cache = None
        adapter._cls = _Resolver
        return adapter

    def test_line_sources_share_instances(self):
        text, sources = self._adapter().assemble_resolved(Path("system.ltx"))

        file_sources = [
            src
            for src in sources
            if src is not None and src.kind == "file"
        ]

        self.assertGreaterEqual(len(file_sources), 2)
        self.assertIs(file_sources[0], file_sources[1])
        self.assertIs(file_sources[0], file_sources[-1])
        self.assertEqual(len(text.split("\n")), len(sources))

    def test_line_sources_carry_source_line_numbers(self):
        adapter = self._adapter()
        adapter._cls = _LineResolver
        _text, sources = adapter.assemble_resolved(Path("system.ltx"))

        file_sources = [
            src
            for src in sources
            if (
                src is not None
                and src.kind == "file"
                and src.line > 0
            )
        ]
        self.assertEqual([src.line for src in file_sources], [2, 7])

    def test_virtual_files_are_loaded_at_their_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            configs = Path(temp) / "configs"
            configs.mkdir()
            root = configs / "system.ltx"
            root.write_text(
                '[system]\nvalue = 1\n#include "defines.ltx"\n',
                encoding="utf-8",
            )
            (configs / "defines.ltx").write_text(
                "[hud_base]\ncost = 100\n",
                encoding="utf-8",
            )

            sections, _sources, duplicates, _parents = (
                LtxResolverAdapter().resolve_with_virtual_files_details(
                    root,
                    {
                        configs / "defines.ltx": (
                            "[hud_base]\ncost = 150\n"
                        ),
                        configs / "mod_system_custom.ltx": (
                            "[scope_base]\nvalue = 2\n"
                        ),
                    },
                )
            )

        self.assertEqual(sections["hud_base"]["cost"], "150")
        self.assertEqual(sections["scope_base"]["value"], "2")
        self.assertEqual(duplicates, {})

    def test_pickle_keeps_shared_line_sources(self):
        text, sources = self._adapter().assemble_resolved(Path("system.ltx"))

        loaded_text, loaded_sources = pickle.loads(
            pickle.dumps((text, sources), protocol=4)
        )

        file_sources = [
            src
            for src in loaded_sources
            if src is not None and src.kind == "file"
        ]
        self.assertGreaterEqual(len(file_sources), 2)
        self.assertIs(file_sources[0], file_sources[1])
        self.assertEqual(loaded_text, text)


if __name__ == "__main__":
    unittest.main()
