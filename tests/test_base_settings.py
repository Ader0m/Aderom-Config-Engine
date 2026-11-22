# -*- coding: utf-8 -*-
"""Автоопределение configs без путей конкретной установки."""

import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.tools.fs import detect_config_dir
from modules.base_module.settings import config_dir_candidates


class ConfigCandidatesTest(unittest.TestCase):
    def test_expected_relative_candidates(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "ACE"
            src = root / "src"
            src.mkdir(parents=True)

            candidates = config_dir_candidates(src)

            self.assertIn(Path.cwd() / "gamedata", candidates)
            self.assertIn(
                root.parent / "Anomaly 1.5.3 up" / "gamedata",
                candidates,
            )
            self.assertIn(root.resolve(), candidates)

    def test_shortcut_relative_game_dir_is_detected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "ACE"
            src = root / "src"
            src.mkdir(parents=True)
            configs = root.parent / "Anomaly 1.5.3 up" / "gamedata" / "configs"
            configs.mkdir(parents=True)
            (configs / "system.ltx").write_text(
                "[system]\n",
                encoding="utf-8",
            )

            found = detect_config_dir(config_dir_candidates(src))

            self.assertIsNotNone(found)
            self.assertEqual(Path(found).resolve(), configs.resolve())

    def test_sibling_game_root_is_discovered(self):
        with tempfile.TemporaryDirectory() as d:
            workspace = Path(d) / "workspace"
            src = workspace / "ACE" / "src"
            src.mkdir(parents=True)
            game = workspace / "Game"
            configs = game / "gamedata" / "configs"
            configs.mkdir(parents=True)
            (configs / "system.ltx").write_text(
                "[system]\n",
                encoding="utf-8",
            )

            candidates = config_dir_candidates(src)

            self.assertIn(game.resolve(), candidates)


if __name__ == "__main__":
    unittest.main()
