# -*- coding: utf-8 -*-
"""Двоичные черновики проекта (.ogg) и их попадание в патч."""

import tempfile
import unittest
from pathlib import Path

from tests.helpers import FakeForest
from tests.support import SRC  # noqa: F401

from core.patching.writer import PatchWriter
from sdk.api import ProjectAssetType
from ui.project import ProjectService
from ui.project_assets import ProjectAssetRegistry


def _registry():
    registry = ProjectAssetRegistry()
    registry.register(
        ProjectAssetType(
            key="sound.ogg",
            label="Звук Ogg Vorbis",
            extensions=(".ogg",),
            vfs_root="sounds",
        )
    )
    return registry


class ProjectAssetDraftTest(unittest.TestCase):
    def _project(self, d):
        service = ProjectService(
            Path(d) / "projects",
            asset_registry=_registry(),
        )
        project = service.create("Sounds")
        return service, project

    def test_asset_draft_persists_and_marks_changes(self):
        with tempfile.TemporaryDirectory() as d:
            sounds = Path(d) / "gamedata" / "sounds"
            sounds.mkdir(parents=True)
            ogg = sounds / "a.ogg"
            ogg.write_bytes(b"ORIGINAL")

            service, project = self._project(d)
            draft = service.update_asset_draft(ogg, b"CHANGED")

            self.assertTrue(draft.has_changes)
            self.assertTrue(draft.is_dirty)
            self.assertEqual(service.get_asset_bytes(ogg), b"CHANGED")
            self.assertEqual(len(service.changed_asset_drafts()), 1)

            service.save_asset_draft(ogg)
            self.assertFalse(draft.is_dirty)
            self.assertEqual(ogg.read_bytes(), b"ORIGINAL")

            project_id = project.id
            service.close()
            service.load(project_id)

            loaded = service.get_asset_draft(ogg)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.current_bytes, b"CHANGED")
            self.assertEqual(loaded.saved_bytes, b"CHANGED")
            self.assertTrue(loaded.has_changes)

    def test_new_asset_draft_is_kept_as_new_file(self):
        with tempfile.TemporaryDirectory() as d:
            sounds = Path(d) / "gamedata" / "sounds"
            sounds.mkdir(parents=True)
            target = sounds / "new.ogg"

            service, _project = self._project(d)
            draft = service.update_asset_draft(target, b"NEW")

            self.assertTrue(draft.is_new)
            self.assertTrue(draft.has_changes)
            self.assertEqual(draft.original_sha1, "")

    def test_asset_preview_and_patch_writer_write_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "gamedata" / "configs"
            cfg.mkdir(parents=True)
            root = cfg / "system.ltx"
            root.write_text("[system]\n", encoding="windows-1251")
            sounds = Path(d) / "gamedata" / "sounds"
            sounds.mkdir(parents=True)
            ogg = sounds / "a.ogg"
            ogg.write_bytes(b"OLD")

            forest = FakeForest(cfg, [root])
            service, _project = self._project(d)
            service.update_asset_draft(ogg, b"NEW-OGG")
            service.save_asset_draft(ogg)

            preview = service.build_preview(None, forest)
            items = [item for item in preview.items if item.kind == "asset"]

            self.assertEqual(len(items), 1)
            item = items[0]
            self.assertTrue(item.ok)
            self.assertEqual(
                item.generated.files[0].relative_path,
                Path("gamedata") / "sounds" / "a.ogg",
            )
            self.assertEqual(item.generated.files[0].data, b"NEW-OGG")

            mod_dir = Path(d) / "mods" / "ACE Patch"
            written = PatchWriter(
                require_verification=True,
                require_resolver=True,
            ).write(
                item.generated,
                mod_dir,
                asset_rules={"sounds": (".ogg",)},
            )

            self.assertEqual(len(written), 1)
            self.assertEqual(written[0].read_bytes(), b"NEW-OGG")

    def test_asset_outside_sounds_is_rejected_in_preview(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "gamedata" / "configs"
            cfg.mkdir(parents=True)
            root = cfg / "system.ltx"
            root.write_text("[system]\n", encoding="windows-1251")
            other = Path(d) / "gamedata" / "textures" / "a.ogg"
            other.parent.mkdir(parents=True)
            other.write_bytes(b"X")

            forest = FakeForest(cfg, [root])
            service, _project = self._project(d)
            service.update_asset_draft(other, b"Y")

            preview = service.build_preview(None, forest)
            items = [item for item in preview.items if item.kind == "asset"]

            self.assertEqual(len(items), 1)
            self.assertFalse(items[0].ok)
            self.assertIn("не зарегистрирован", items[0].error)


if __name__ == "__main__":
    unittest.main()
