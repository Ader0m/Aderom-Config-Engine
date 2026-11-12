# -*- coding: utf-8 -*-
"""Реестр читателей файлов: проект получает итоговый вид от модуля."""

import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from sdk import FileReader, FileView
from ui.project_readers import FileReaderError, ProjectFileReaderRegistry


class _Reader(FileReader):
    def __init__(self, suffix, text, priority=100):
        self.suffix = suffix
        self.text = text
        self.priority = priority

    def can_read(self, path, data=None):
        return Path(path).suffix.lower() == self.suffix

    def read(self, path, data=None):
        return FileView(text=self.text, label=type(self).__name__)


class ProjectFileReaderRegistryTest(unittest.TestCase):
    def test_priority_and_predicate_select_one_reader(self):
        registry = ProjectFileReaderRegistry()
        registry.register(_Reader(".ogg", "low", priority=10))
        registry.register(_Reader(".ogg", "high", priority=200))

        view = registry.read(Path("a.ogg"), b"data")

        self.assertEqual(view.text, "high")
        self.assertEqual(view.label, "_Reader")

    def test_no_matching_reader_returns_none(self):
        registry = ProjectFileReaderRegistry()
        registry.register(_Reader(".ogg", "text"))
        self.assertIsNone(registry.read(Path("a.txt"), b"x"))

    def test_unsubscribe_removes_reader(self):
        registry = ProjectFileReaderRegistry()
        subscription = registry.register(_Reader(".ogg", "text"))
        subscription.unsubscribe()
        subscription.unsubscribe()
        self.assertIsNone(registry.read(Path("a.ogg"), b"x"))

    def test_reader_error_is_reported(self):
        class Broken(FileReader):
            def can_read(self, path, data=None):
                return True

            def read(self, path, data=None):
                raise ValueError("boom")

        registry = ProjectFileReaderRegistry()
        registry.register(Broken())

        with self.assertRaises(FileReaderError):
            registry.read(Path("a.ogg"), b"x")

    def test_string_result_is_wrapped(self):
        class TextReader(FileReader):
            def can_read(self, path, data=None):
                return True

            def read(self, path, data=None):
                return "plain view"

        registry = ProjectFileReaderRegistry()
        registry.register(TextReader())

        view = registry.read(Path("a.ogg"), b"x")
        self.assertEqual(view.text, "plain view")


if __name__ == "__main__":
    unittest.main()
