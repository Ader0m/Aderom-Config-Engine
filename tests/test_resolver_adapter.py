# -*- coding: utf-8 -*-
import pickle
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.resolver_adapter import LtxResolverAdapter


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
