# -*- coding: utf-8 -*-
"""Индексы целей патча должны переживать добавление виртуальных файлов."""

import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401
from tests.helpers import write_text

from core.patching.targets import root_candidates
from core.tools.cache import FileCacheSystem
from core.tree.builder import TreeBuilder, add_virtual_file


class TargetIndexInvalidationTest(unittest.TestCase):
    def test_virtual_dltx_file_uses_fresh_parent_index(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "gamedata" / "configs"
            system = write_text(
                cfg / "system.ltx",
                '[system]\n#include "items/item_base.ltx"\n',
            )
            item_base = write_text(
                cfg / "items" / "item_base.ltx",
                "[base_item]\ncost = 1\n",
            )
            forest = TreeBuilder(FileCacheSystem()).build(
                cfg,
                system,
                use_cache=False,
            )

            # Прогреваем индекс до добавления виртуального файла.
            self.assertIn(
                system,
                root_candidates(forest, item_base),
            )

            mod_path = cfg / "items" / "mod_item_base_~ace_test.ltx"
            self.assertTrue(
                add_virtual_file(
                    forest,
                    mod_path,
                    "[new_item]:base_item\ncost = 2\n",
                )
            )

            # Без сброса индекса здесь вернулся бы только сам mod-файл.
            candidates = root_candidates(forest, mod_path)
            self.assertIn(system, candidates)
            self.assertNotEqual(candidates, (mod_path,))


if __name__ == "__main__":
    unittest.main()
