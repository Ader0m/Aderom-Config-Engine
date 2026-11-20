# -*- coding: utf-8 -*-
"""Режимы проверки поштучных LTX-патчей.

В Root-режиме поштучный патч заменяется объединённым, поэтому дорогой
проход Resolver4 на каждый файл не нужен. Модельная round-trip проверка
при этом должна остаться: она ловит ошибки наследования и списков,
которые resolver-проверка объединённого плана не покрывает.
"""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.patching.ltx.generator import DltxGenerator
from core.patching.ltx.service import LtxPatchService
from core.patching.models import VerificationResult


class _Forest:
    def __init__(self, cfg, files):
        self.scan_dir = str(cfg)
        self.files = [Path(path) for path in files]
        self.include_parents = {}


class _CountingServices:
    def __init__(self):
        self.generate_calls = 0

    def generate_patch(self, plan):
        self.generate_calls += 1
        generated = DltxGenerator().generate(plan)
        return replace(
            generated,
            verification=VerificationResult(ok=True),
        )


class LtxPatchVerifyModesTest(unittest.TestCase):
    def _setup(self, d):
        cfg = Path(d) / "configs"
        cfg.mkdir(parents=True)
        root = cfg / "system.ltx"
        source = cfg / "a.ltx"
        root.write_text('[system]\n#include "a.ltx"\n', encoding="cp1251")
        source.write_text("[w]\ncost = 100\n", encoding="cp1251")
        forest = _Forest(cfg, [root, source])
        return source, forest

    def test_model_only_verification_skips_resolver(self):
        with tempfile.TemporaryDirectory() as d:
            source, forest = self._setup(d)
            services = _CountingServices()
            patch = LtxPatchService(services)

            result = patch.plan(
                source,
                "[w]\ncost = 100\n",
                "[w]\ncost = 150\n",
                forest=forest,
                verify_resolver=False,
            )

            self.assertEqual(services.generate_calls, 0)
            self.assertIsNotNone(result.generated)
            self.assertIsNotNone(result.generated.verification)
            self.assertTrue(result.generated.verification.ok)
            self.assertTrue(
                any(
                    check.name.startswith("parents:")
                    for check in result.generated.verification.checks
                ),
                result.generated.verification.checks,
            )

    def test_default_still_uses_full_resolver_generation(self):
        with tempfile.TemporaryDirectory() as d:
            source, forest = self._setup(d)
            services = _CountingServices()
            patch = LtxPatchService(services)

            result = patch.plan(
                source,
                "[w]\ncost = 100\n",
                "[w]\ncost = 150\n",
                forest=forest,
            )

            self.assertEqual(services.generate_calls, 1)
            self.assertIsNotNone(result.generated)
            self.assertTrue(result.generated.verification.ok)


if __name__ == "__main__":
    unittest.main()
