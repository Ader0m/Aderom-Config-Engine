# -*- coding: utf-8 -*-
"""Реестр типов ресурсов проекта: модули добавляют поддержку сами."""

import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.patching.writer import PatchWriter
from sdk import FileReader, FileView
from sdk.api import ProjectAssetType
from tests.helpers import FakeForest
from app.projects import ProjectService
from app.projects import ProjectAssetRegistry, ProjectAssetTypeError
from app.projects import ProjectFileReaderRegistry


def _type(key, root, extensions):
    return ProjectAssetType(
        key=key,
        label=key,
        extensions=tuple(extensions),
        vfs_root=root,
    )


class ProjectAssetRegistryTest(unittest.TestCase):
    def test_register_find_and_unregister(self):
        registry = ProjectAssetRegistry()
        registry.register(_type("sound.ogg", "sounds", (".OGG",)))

        relative, asset_type = registry.relative_for_path(
            Path("C:/game/gamedata"),
            Path("C:/game/gamedata/sounds/ambient/a.ogg"),
        )

        self.assertEqual(relative, Path("sounds/ambient/a.ogg"))
        self.assertEqual(asset_type.key, "sound.ogg")
        self.assertEqual(registry.patch_rules(), {"sounds": (".ogg",)})

        self.assertTrue(registry.unregister("sound.ogg"))
        self.assertEqual(registry.patch_rules(), {})
        with self.assertRaises(ProjectAssetTypeError):
            registry.relative_for_path(
                Path("C:/game/gamedata"),
                Path("C:/game/gamedata/sounds/ambient/a.ogg"),
            )

    def test_duplicate_and_conflict_are_rejected(self):
        registry = ProjectAssetRegistry()
        registry.register(_type("sound.ogg", "sounds", (".ogg",)))

        with self.assertRaises(ProjectAssetTypeError):
            registry.register(_type("sound.ogg", "sounds", (".ogg",)))

        with self.assertRaises(ProjectAssetTypeError):
            registry.register(_type("sound.other", "sounds", (".ogg",)))

        registry.register(_type("texture.dds", "textures", (".dds",)))
        self.assertEqual(
            registry.patch_rules(),
            {"sounds": (".ogg",), "textures": (".dds",)},
        )

        with self.assertRaises(ProjectAssetTypeError):
            registry.register(_type("config.ogg", "configs", (".ogg",)))

    def test_path_outside_game_root_is_rejected(self):
        registry = ProjectAssetRegistry()
        registry.register(_type("sound.ogg", "sounds", (".ogg",)))

        with self.assertRaises(ProjectAssetTypeError):
            registry.relative_for_path(
                Path("C:/game/gamedata"),
                Path("C:/other/sounds/a.ogg"),
            )


class ProjectRegistryWiringTest(unittest.TestCase):
    def test_empty_registry_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as d:
            registry = ProjectAssetRegistry()
            service = ProjectService(
                Path(d) / "projects",
                asset_registry=registry,
            )
            self.assertIs(service.asset_registry, registry)

            registry.register(_type("sound.ogg", "sounds", (".ogg",)))
            self.assertIs(
                service.asset_registry,
                registry,
            )
            relative, asset_type = service.asset_registry.relative_for_path(
                Path("C:/game/gamedata"),
                Path("C:/game/gamedata/sounds/a.ogg"),
            )
            self.assertEqual(relative, Path("sounds/a.ogg"))
            self.assertEqual(asset_type.key, "sound.ogg")


class GenericAssetPreviewTest(unittest.TestCase):
    def test_registered_type_reaches_patch_writer(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "gamedata" / "configs"
            cfg.mkdir(parents=True)
            root = cfg / "system.ltx"
            root.write_text("[system]\n", encoding="windows-1251")
            texture = Path(d) / "gamedata" / "textures" / "a.dds"
            texture.parent.mkdir(parents=True)
            texture.write_bytes(b"OLD")

            registry = ProjectAssetRegistry()
            registry.register(_type("texture.dds", "textures", (".dds",)))

            readers = ProjectFileReaderRegistry()

            class DdsReader(FileReader):
                def can_read(self, path, data=None):
                    return Path(path).suffix.lower() == ".dds"

                def read(self, path, data=None):
                    return FileView(
                        text="DDS VIEW",
                        label="DDS",
                        details=("format: DXT5",),
                    )

            readers.register(DdsReader())

            service = ProjectService(
                Path(d) / "projects",
                asset_registry=registry,
                file_readers=readers,
            )
            project = service.create("Textures")
            service.update_asset_draft(texture, b"NEW-DDS")
            service.save_asset_draft(texture)

            preview = service.build_preview(
                None,
                FakeForest(cfg, [root]),
            )
            items = [item for item in preview.items if item.kind == "asset"]

            self.assertEqual(len(items), 1)
            item = items[0]
            self.assertTrue(item.ok)
            self.assertEqual(item.plan.patch_target_dir, "textures")
            self.assertEqual(item.text, "DDS VIEW")
            self.assertIn("format: DXT5", item.notes)
            self.assertEqual(
                item.generated.files[0].relative_path,
                Path("gamedata") / "textures" / "a.dds",
            )

            written = PatchWriter(
                require_verification=True,
                require_resolver=True,
            ).write(
                item.generated,
                Path(d) / "mods" / project.name,
                asset_rules=registry.patch_rules(),
            )
            self.assertEqual(written[0].read_bytes(), b"NEW-DDS")


if __name__ == "__main__":
    unittest.main()
