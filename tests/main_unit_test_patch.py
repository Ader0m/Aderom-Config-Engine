# -*- coding: utf-8 -*-
"""Эталонный тест патчинга LTX: game.ltx и его include-файлы.

ВАЖНО: файлы ``main_unit_test_*`` нельзя создавать без команды оператора
и нельзя изменять без его положительного отклика. Подробнее см.
``docs/main_unit_tests.md``.

Набор данных повторяет поведение реального ``game.ltx`` Anomaly:

* ``game.ltx`` подключает ``game_maps_single.ltx`` и содержит
  ``[agroprom_musics]``;
* изменяемая строка ``music\\marsh_2`` вместе с секцией
  ``[___level_music_tracks]`` принадлежит ``game_maps_single.ltx``.

Ожидаемые результаты режимов Root / On-file / Override:

* Root и On-file привязывают патч к корню загрузки ``game.ltx``;
* Override копирует файл-победитель ``game_maps_single.ltx`` целиком.
"""

import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.resolver.assembly import AssemblyService
from core.facade import CoreServices
from core.tools.cache import FileCacheSystem
from core.tree.graph import DisabledCache, ReferenceGraphBuilder
from core.tools.cache import ResolvedCache
from core.resolver.service import ResolverService
from core.tree.builder import TreeBuilder
from core.tree.query import TreeQueryService
from core.xml.service import XmlResolveService
from ui.project import ProjectService


GAME_LTX = r'''#include "game_maps_single.ltx"

[agroprom_musics]
music\level\_shared\music_01 = 0,24,0.40,20,40
music\level\_shared\music_02 = 0,24,0.40,20,40
music\level\_shared\music_03 = 0,24,0.30,20,40
music\level\_shared\music_04 = 0,24,0.30,20,40
music\level\_shared\music_05 = 0,24,0.30,20,40
music\level\_shared\music_06 = 0,24,0.40,20,40
'''

GAME_MAPS_SINGLE_LTX = r'''[___level_music_tracks]
music\marsh_2 = 8,24,0.25,5,25
'''

OLD_LINE = "music\\marsh_2 = 8,24,0.25,5,25"
NEW_LINE = "music\\marsh_2 = 66,66,66,66,66"

EXPECTED_RESOLVED = r'''[___level_music_tracks]
music\marsh_2 = 8,24,0.25,5,25

[agroprom_musics]
music\level\_shared\music_01 = 0,24,0.40,20,40
music\level\_shared\music_02 = 0,24,0.40,20,40
music\level\_shared\music_03 = 0,24,0.30,20,40
music\level\_shared\music_04 = 0,24,0.30,20,40
music\level\_shared\music_05 = 0,24,0.30,20,40
music\level\_shared\music_06 = 0,24,0.40,20,40
'''

EXPECTED_OVERRIDE = r'''[___level_music_tracks]
music\marsh_2 = 66,66,66,66,66
'''

CONFIGS = Path("gamedata") / "configs"


def _normalize(text):
    """Единый вид текста для сравнений: LF и без хвостовых пробелов."""
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalized.split("\n")]
    return "\n".join(lines).strip()


def _resolved_body(text):
    """Убрать служебные комментарии адаптера Resolver4 из resolved-текста."""
    lines = _normalize(text).split("\n")
    while lines and (
        lines[0].startswith("; Assembled root:")
        or lines[0].startswith("; Resolver module:")
        or not lines[0].strip()
    ):
        lines.pop(0)
    return "\n".join(lines).strip()


def _expected_dltx(source_path, root_path):
    """Ожидаемый DLTX-патч замены одной строки."""
    return (
        "; ACE generated DLTX patch\n"
        "; Source: %s\n"
        "; Root: %s\n"
        "; Changes: 1\n"
        "\n"
        "![___level_music_tracks]\n"
        "music\\marsh_2 = 66,66,66,66,66\n"
        % (source_path, root_path)
    )


class PatchDataSetBase(unittest.TestCase):
    """Общая тестовая VFS-среда с game.ltx и его include-файлами."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.config = self.base / CONFIGS
        self.config.mkdir(parents=True)

        self.root = self.config / "game.ltx"
        self.maps = self.config / "game_maps_single.ltx"

        self._write(self.root, GAME_LTX)
        self._write(self.maps, GAME_MAPS_SINGLE_LTX)

        self.cache = FileCacheSystem()
        self.forest = TreeBuilder(cache=self.cache).build(
            self.config,
            self.root,
            use_cache=False,
        )
        self.resolver = ResolverService(
            cache=self.cache,
            resolved_cache=ResolvedCache(),
        )
        self.resolver.ensure_adapter(self.forest, self.config)

        self.resolved_text, self.line_sources = (
            self.resolver.assemble_resolved(
                self.root,
                self.forest,
                self.config,
            )
        )
        self.edited_text = self.resolved_text.replace(OLD_LINE, NEW_LINE)
        self.assertNotEqual(self.resolved_text, self.edited_text)

    @staticmethod
    def _write(path, text):
        path.write_text(text, encoding="cp1251", newline="\n")

    def _source_for(self, line_text):
        for line, source in zip(
            self.resolved_text.splitlines(),
            self.line_sources,
        ):
            if line.strip() == line_text:
                return source
        self.fail("Строка не найдена в resolved-тексте: %s" % line_text)


class ResolveDataSetTest(PatchDataSetBase):
    """Первая проверка: include дополняет game.ltx до ожидаемого вида."""

    def test_game_maps_single_supplements_game_ltx(self):
        self.assertEqual(
            _resolved_body(self.resolved_text),
            _normalize(EXPECTED_RESOLVED),
        )

    def test_changed_line_is_owned_by_game_maps_single(self):
        source = self._source_for(OLD_LINE)

        self.assertIsNotNone(source)
        self.assertEqual(source.kind, "file")
        self.assertEqual(Path(source.path).name, "game_maps_single.ltx")


class PatchModesTest(PatchDataSetBase):
    """Патчинг в трёх режимах проекта: Root, On-file, Override."""

    def _services(self):
        return CoreServices(
            cache=self.cache,
            tree_builder=TreeBuilder(self.cache),
            tree_query=TreeQueryService(),
            graph_builder=ReferenceGraphBuilder(DisabledCache()),
            resolver_service=self.resolver,
            assembly_service=AssemblyService(
                self.resolver,
                self.base / "generated",
            ),
            generated_dir=self.base / "generated",
            xml_resolve_service=XmlResolveService(),
        )

    def _preview_item(self, mode):
        projects = ProjectService(self.base / ("projects_" + mode))
        projects.create("override", save_mode=mode)
        projects.update_resolved_draft(
            "resolved:game",
            self.root,
            "game.ltx (resolved)",
            self.resolved_text,
            self.edited_text,
            line_sources=self.line_sources,
        )
        preview = projects.build_preview(self._services(), self.forest)

        self.assertEqual(len(preview.items), 1, preview.items)
        item = preview.items[0]
        self.assertTrue(item.ok, item.error)
        self.assertIsNotNone(item.generated)
        self.assertEqual(len(item.generated.files), 1)
        return item

    def test_root_mode_patches_game_ltx(self):
        item = self._preview_item("root")
        file = item.generated.files[0]

        self.assertEqual(
            file.relative_path,
            CONFIGS / "mod_game_~ace_override.ltx",
        )
        self.assertEqual(
            _normalize(file.text),
            _normalize(_expected_dltx(self.root, self.root)),
        )

    def test_on_file_mode_patches_load_root(self):
        item = self._preview_item("on_file")
        file = item.generated.files[0]

        self.assertEqual(item.plan.format, "ltx")
        self.assertEqual(
            file.relative_path,
            CONFIGS / "mod_game_~ace_override.ltx",
        )
        self.assertEqual(
            _normalize(file.text),
            _normalize(_expected_dltx(self.root, self.root)),
        )

    def test_override_mode_copies_line_owner(self):
        item = self._preview_item("override")
        file = item.generated.files[0]

        self.assertEqual(item.plan.format, "ltx_override")
        self.assertEqual(
            file.relative_path,
            CONFIGS / "game_maps_single.ltx",
        )
        self.assertEqual(
            _normalize(file.text),
            _normalize(EXPECTED_OVERRIDE),
        )


if __name__ == "__main__":
    unittest.main()
