# -*- coding: utf-8 -*-
"""config/modules.json: пользовательское включение модулей (ADR-31/34)."""

import json
import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from ui.platform.module_config import ModuleConfigStore


class ModuleConfigStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "config" / "modules.json"

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def test_missing_file_uses_defaults(self):
        store = ModuleConfigStore(self.path)

        self.assertFalse(self.path.exists())
        self.assertFalse(store.read_only)
        self.assertTrue(store.enabled_for("demo", True))
        self.assertFalse(store.enabled_for("demo", False))
        self.assertEqual(store.modules(), {})

    def test_explicit_enable_is_written_atomically(self):
        store = ModuleConfigStore(self.path)

        self.assertTrue(store.set_enabled("demo", True))

        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["modules"]["demo"]["enabled"], True)
        self.assertEqual(
            list(self.path.parent.glob("modules.json.tmp.*")),
            [],
        )

    def test_unknown_ids_and_other_entries_are_preserved(self):
        self._write({
            "schema_version": 1,
            "modules": {
                "ghost": {"enabled": True},
                "base": {"enabled": False},
            },
        })
        store = ModuleConfigStore(self.path)

        self.assertTrue(store.set_enabled("base", True))

        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(
            data["modules"],
            {
                "base": {"enabled": True},
                "ghost": {"enabled": True},
            },
        )

    def test_future_schema_is_read_only_and_not_interpreted(self):
        self._write({
            "schema_version": 2,
            "modules": {"base": {"enabled": False}},
        })
        before = self.path.read_text(encoding="utf-8")
        logs = []
        store = ModuleConfigStore(self.path, log=logs.append)

        self.assertTrue(store.read_only)
        self.assertTrue(store.enabled_for("base", True))
        self.assertFalse(store.set_enabled("base", True))
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)
        self.assertFalse(
            list(self.path.parent.glob("modules.json.broken-*"))
        )
        self.assertTrue(any("read-only" in line for line in logs))

    def test_broken_json_is_backed_up_and_defaults_are_used(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{", encoding="utf-8")
        logs = []
        store = ModuleConfigStore(self.path, log=logs.append)

        self.assertTrue(store.broken)
        self.assertTrue(store.enabled_for("base", True))
        backups = list(self.path.parent.glob("modules.json.broken-*"))
        self.assertEqual(len(backups), 1)
        self.assertFalse(self.path.exists())

        self.assertTrue(store.set_enabled("base", False))
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["modules"]["base"]["enabled"], False)
        self.assertTrue(any("повреждён" in line for line in logs))

    def test_invalid_module_entry_is_treated_as_broken(self):
        self._write({
            "schema_version": 1,
            "modules": {"base": {"enabled": "yes"}},
        })
        store = ModuleConfigStore(self.path)

        self.assertTrue(store.broken)
        self.assertTrue(store.enabled_for("base", True))
        self.assertTrue(
            list(self.path.parent.glob("modules.json.broken-*"))
        )

    def test_missing_schema_version_is_treated_as_broken(self):
        self._write({"modules": {}})
        store = ModuleConfigStore(self.path)

        self.assertTrue(store.broken)


if __name__ == "__main__":
    unittest.main()
