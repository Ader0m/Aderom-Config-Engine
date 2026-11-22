# -*- coding: utf-8 -*-
"""Проверка целостности эталонного манифеста Этапа 0."""

import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "baseline" / "stage0-manifest.json"


class BaselineManifestTest(unittest.TestCase):
    def test_manifest_signature_and_counts(self):
        self.assertTrue(MANIFEST.is_file(), MANIFEST)
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(data["format"], "ace-baseline-manifest-v1")
        payload = {
            key: value
            for key, value in data.items()
            if key not in ("signature", "generated_at")
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        self.assertEqual(
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            data["signature"],
        )

        sections = data["sections"]
        counts = data["counts"]
        self.assertEqual(counts["sections"], len(sections))
        self.assertEqual(counts["keys"], sum(sections.values()))
        self.assertEqual(counts["ltx_files"], len(data["ltx_files"]))
        self.assertEqual(counts["xml_files"], len(data["xml_files"]))
        self.assertEqual(counts["roots"], len(data["roots"]))
        self.assertEqual(
            counts["system_tree_files"],
            len(data["system_tree_files"]),
        )
        self.assertGreater(counts["sections"], 0)
        self.assertGreater(counts["keys"], 0)


if __name__ == "__main__":
    unittest.main()
