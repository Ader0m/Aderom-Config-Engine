# -*- coding: utf-8 -*-
"""Хранилище черновиков: формат, сжатие, чтение и удаление."""

import json
import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.api.documents import LineSource

from app.projects.drafts import (
    LINE_SOURCE_IDS_VERSION,
    RESOLVED_DRAFT_VERSION,
    DraftStore,
)
from app.projects.models import (
    ProjectAssetDraft,
    ProjectDraft,
    ProjectResolvedDraft,
)

BIG_TEXT = "[system]\n" + ("x = 1\n" * 4000)


def _text_draft(path: Path, current: str, original: str = "") -> ProjectDraft:
    return ProjectDraft(
        path=path,
        original_text=original,
        current_text=current,
        saved_text=original,
        is_new=False,
        revision=2,
        updated_at=10.0,
        saved_at=5.0,
    )


def _resolved_draft(
    root: Path,
    current: str,
    sources=(),
    original: str = "",
) -> ProjectResolvedDraft:
    return ProjectResolvedDraft(
        root_path=root,
        title="system.ltx",
        original_text=original,
        current_text=current,
        saved_text=original,
        tab_id="tab-1",
        line_sources=tuple(sources),
        revision=3,
        updated_at=10.0,
        saved_at=5.0,
    )


class TextDraftStorageTest(unittest.TestCase):
    def test_small_text_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            store = DraftStore()
            source = Path(d) / "a.ltx"
            draft = _text_draft(source, "[a]\nx = 2\n", "[a]\nx = 1\n")

            payload = store.build_text_payload(draft)
            self.assertEqual(payload["current_text"], "[a]\nx = 2\n")
            self.assertEqual(payload["original_text"], "[a]\nx = 1\n")
            self.assertNotIn("saved_text", payload)

            store.write_payload(
                store.text_target(Path(d), source),
                payload,
            )
            loaded = store.read_text_drafts(Path(d))
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].path, source)
            self.assertEqual(loaded[0].current_text, "[a]\nx = 2\n")
            self.assertEqual(loaded[0].original_text, "[a]\nx = 1\n")
            self.assertEqual(loaded[0].saved_text, "[a]\nx = 2\n")
            self.assertEqual(loaded[0].revision, 2)

    def test_large_text_is_compressed(self):
        with tempfile.TemporaryDirectory() as d:
            store = DraftStore()
            source = Path(d) / "system.ltx"
            draft = _text_draft(source, BIG_TEXT)

            payload = store.build_text_payload(draft)
            self.assertEqual(
                payload["current_text"]["encoding"],
                "zlib+base64",
            )

            store.write_payload(
                store.text_target(Path(d), source),
                payload,
            )
            loaded = store.read_text_drafts(Path(d))
            self.assertEqual(loaded[0].current_text, BIG_TEXT)

    def test_corrupted_draft_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            store = DraftStore()
            folder = Path(d) / "drafts"
            folder.mkdir()
            (folder / "broken.json").write_text("{", encoding="utf-8")
            good = _text_draft(Path(d) / "good.ltx", "[a]\nx = 1\n")
            store.write_payload(
                store.text_target(Path(d), good.path),
                store.build_text_payload(good),
            )

            loaded = store.read_text_drafts(Path(d))
            self.assertEqual([draft.path for draft in loaded], [good.path])

    def test_delete_text_removes_file_and_ignores_missing(self):
        with tempfile.TemporaryDirectory() as d:
            store = DraftStore()
            source = Path(d) / "a.ltx"
            target = store.text_target(Path(d), source)
            store.write_payload(
                target,
                store.build_text_payload(_text_draft(source, "x")),
            )
            self.assertTrue(target.is_file())

            store.delete_text(Path(d), source)
            self.assertFalse(target.is_file())
            store.delete_text(Path(d), source)


class AssetDraftStorageTest(unittest.TestCase):
    def test_saved_bytes_are_not_duplicated(self):
        with tempfile.TemporaryDirectory() as d:
            store = DraftStore()
            asset = Path(d) / "sound.ogg"
            draft = ProjectAssetDraft(
                path=asset,
                original_sha1="",
                current_bytes=b"OGG",
                saved_bytes=b"OGG",
                is_new=True,
                revision=1,
            )
            payload = store.build_asset_payload(draft)
            self.assertNotIn("saved_b64", payload)

            draft.saved_bytes = b""
            payload = store.build_asset_payload(draft)
            self.assertIn("saved_b64", payload)

            store.write_payload(store.asset_target(Path(d), asset), payload)
            loaded = store.read_asset_drafts(Path(d))
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].current_bytes, b"OGG")
            self.assertEqual(loaded[0].saved_bytes, b"")


class ResolvedDraftStorageTest(unittest.TestCase):
    def test_payload_has_current_format_and_compact_sources(self):
        with tempfile.TemporaryDirectory() as d:
            store = DraftStore()
            root = Path(d) / "configs"
            source = LineSource(
                kind="key",
                display="system.ltx:1",
                path=Path(d) / "system.ltx",
                line=1,
            )
            draft = _resolved_draft(root, "[a]\nx = 1\n", [source, None])

            payload = store.build_resolved_payload(draft)
            self.assertEqual(payload["version"], RESOLVED_DRAFT_VERSION)
            self.assertEqual(
                payload["line_sources"]["version"],
                LINE_SOURCE_IDS_VERSION,
            )
            self.assertEqual(len(payload["line_sources"]["sources"]), 1)

            store.write_payload(
                store.resolved_target(Path(d), draft),
                payload,
            )
            loaded = store.read_resolved_drafts(Path(d))
            self.assertEqual(len(loaded), 1)
            _, draft = loaded[0]
            self.assertEqual(draft.current_text, "[a]\nx = 1\n")
            self.assertEqual(len(draft.line_sources), 2)
            self.assertEqual(draft.line_sources[0].kind, "key")
            self.assertIsNone(draft.line_sources[1])

    def test_doubled_saved_text_is_collapsed(self):
        with tempfile.TemporaryDirectory() as d:
            store = DraftStore()
            root = Path(d) / "configs"
            text = "[a]\nx = 1\n"
            draft = _resolved_draft(root, text)
            payload = store.build_resolved_payload(
                draft,
                saved_text=text + text,
            )
            self.assertNotIn("saved_text", payload)

    def test_legacy_source_list_is_read(self):
        with tempfile.TemporaryDirectory() as d:
            store = DraftStore()
            root = Path(d) / "configs"
            folder = Path(d) / "resolved"
            folder.mkdir()
            target = folder / "legacy.json"
            target.write_text(
                json.dumps(
                    {
                        "tab_id": "tab-1",
                        "root_path": str(root),
                        "title": "system.ltx",
                        "current_text": "[a]\nx = 1\n",
                        "line_sources": [
                            {
                                "kind": "key",
                                "display": "system.ltx:1",
                                "path": "",
                                "line": 1,
                                "open_raw": False,
                            },
                            None,
                        ],
                        "revision": 1,
                        "updated_at": 1.0,
                        "saved_at": 2.0,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            loaded = store.read_resolved_drafts(Path(d))
            self.assertEqual(len(loaded), 1)
            draft = loaded[0][1]
            self.assertEqual(draft.current_text, "[a]\nx = 1\n")
            self.assertEqual(len(draft.line_sources), 2)
            self.assertEqual(draft.line_sources[0].kind, "key")
            self.assertIsNone(draft.line_sources[1])

    def test_corrupted_resolved_draft_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            store = DraftStore()
            folder = Path(d) / "resolved"
            folder.mkdir()
            (folder / "broken.json").write_text("[]", encoding="utf-8")

            self.assertEqual(store.read_resolved_drafts(Path(d)), [])


class SharedLockTest(unittest.TestCase):
    def test_store_uses_injected_write_lock(self):
        import threading

        lock = threading.RLock()
        store = DraftStore(lock)
        self.assertIs(store._write_lock, lock)


if __name__ == "__main__":
    unittest.main()
