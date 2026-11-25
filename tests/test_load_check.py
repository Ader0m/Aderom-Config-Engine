# -*- coding: utf-8 -*-
"""Лёгкие проверки инструмента load_check."""

import contextlib
import importlib.util
import io
import json
import logging
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support import SRC  # noqa: F401

from core.resolver.service import ResolverService
from core.tools.cache import FileCacheSystem, ResolvedCache
from core.tree.builder import TreeBuilder


class LoadCheckTest(unittest.TestCase):
    @staticmethod
    def _load_tool():
        path = Path(__file__).resolve().parents[1] / "tools" / "load_check.py"
        spec = importlib.util.spec_from_file_location(
            "ace_load_check",
            path,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_generate_synthetic_vfs_and_open(self):
        tool = self._load_tool()
        with tempfile.TemporaryDirectory() as d:
            configs = Path(d) / "gamedata" / "configs"
            generated = tool.generate_synthetic_vfs(
                configs,
                sections=10,
                files=15,
            )
            self.assertEqual(generated["files"], 15)
            self.assertEqual(generated["sections"], 15)

            forest = TreeBuilder(cache=FileCacheSystem()).build(
                configs,
                generated["system"],
                use_cache=False,
            )
            self.assertEqual(len(forest.files), 15)
            self.assertEqual(len(forest.roots), 4)

            resolver = ResolverService(
                FileCacheSystem(),
                ResolvedCache(),
            )
            text, _sources = resolver.assemble_resolved(
                generated["system"],
                forest,
                configs,
            )
            self.assertGreaterEqual(
                len([line for line in text.splitlines()
                     if line.startswith("[")]),
                12,
            )

    def test_generate_rejects_impossible_sizes(self):
        tool = self._load_tool()
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                tool.generate_synthetic_vfs(
                    Path(d) / "configs",
                    sections=10,
                    files=11,
                )

    def test_cli_skip_prebuild_writes_json(self):
        tool = self._load_tool()
        with tempfile.TemporaryDirectory() as d:
            report_path = Path(d) / "report.json"
            output = io.StringIO()
            previous_disable = logging.root.manager.disable
            try:
                with contextlib.redirect_stdout(output):
                    code = tool.main([
                        "--sections", "5",
                        "--files", "8",
                        "--skip-prebuild",
                        "--workdir", d,
                        "--json", str(report_path),
                    ])
            finally:
                logging.disable(previous_disable)
            self.assertEqual(code, 0)
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["format"], "ace-load-check-v1")
            self.assertEqual(payload["counts"]["files"], 8)
            self.assertEqual(payload["counts"]["sections"], 8)
            self.assertTrue(payload["ok"])


    def test_evaluate_ok_requires_completed_prebuild_and_real_cancel(self):
        tool = self._load_tool()
        counts = {
            "files": 20000,
            "resolved_sections": 19000,
            "roots": 999,
        }
        prebuild = {
            "state": "done",
            "error": None,
            "done": 999,
            "total": 999,
        }
        cancel = {
            "state": "idle",
            "error": None,
            "stopped": True,
            "done": 0,
            "total": 999,
        }
        self.assertTrue(tool.evaluate_ok(counts, prebuild, cancel))

        cases = [
            ("files", {**counts, "files": 19999}, prebuild, cancel),
            ("roots", {**counts, "roots": 998}, prebuild, cancel),
            (
                "prebuild done",
                counts,
                {**prebuild, "done": 998},
                cancel,
            ),
            (
                "prebuild total",
                counts,
                {**prebuild, "total": 998},
                cancel,
            ),
            (
                "prebuild error",
                counts,
                {**prebuild, "error": "boom"},
                cancel,
            ),
            (
                "cancel not stopped",
                counts,
                prebuild,
                {**cancel, "state": "done", "stopped": False},
            ),
            (
                "cancel error",
                counts,
                prebuild,
                {**cancel, "error": "boom"},
            ),
            (
                "cancel completed",
                counts,
                prebuild,
                {**cancel, "done": 999, "total": 999},
            ),
        ]
        for name, case_counts, case_prebuild, case_cancel in cases:
            with self.subTest(name=name):
                self.assertFalse(
                    tool.evaluate_ok(
                        case_counts,
                        case_prebuild,
                        case_cancel,
                    )
                )

    def test_run_check_with_fake_prebuild(self):
        tool = self._load_tool()

        class _FakePrebuild:
            instances = []

            def __init__(self, resolver, workers=None):
                self.index = len(self.instances)
                self.instances.append(self)
                self.started = False
                self.stopped = False

            def start(self, forest, config_dir):
                self.started = True

            def status(self):
                if self.index == 0:
                    return {
                        "state": "done",
                        "done": 999,
                        "total": 999,
                        "error": None,
                    }
                if not self.stopped:
                    return {
                        "state": "running",
                        "done": 0,
                        "total": 999,
                        "error": None,
                    }
                return {
                    "state": "idle",
                    "done": 0,
                    "total": 999,
                    "error": None,
                }

            def stop(self):
                self.stopped = True

            def wait(self, timeout=None):
                pass

        _FakePrebuild.instances = []
        with tempfile.TemporaryDirectory() as d:
            configs = Path(d) / "gamedata" / "configs"
            generated = tool.generate_synthetic_vfs(
                configs,
                sections=5,
                files=8,
            )
            with patch.object(
                tool,
                "PrebuildService",
                _FakePrebuild,
            ):
                report = tool.run_check(
                    configs,
                    generated["system"],
                    workers=2,
                    timeout=10,
                )
        self.assertFalse(report["ok"])
        self.assertEqual(report["prebuild"]["done"], 999)
        self.assertEqual(report["prebuild"]["total"], 999)
        self.assertTrue(report["cancel"]["stopped"])
        self.assertEqual(report["cancel"]["done"], 0)
        self.assertEqual(len(_FakePrebuild.instances), 2)
        self.assertTrue(all(item.started for item in _FakePrebuild.instances))
        self.assertTrue(_FakePrebuild.instances[1].stopped)

    def test_cli_keep_preserves_temporary_workdir(self):
        tool = self._load_tool()
        errors = io.StringIO()
        previous_disable = logging.root.manager.disable
        workdir = None
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(errors):
                code = tool.main([
                    "--sections", "3",
                    "--files", "5",
                    "--skip-prebuild",
                    "--keep",
                ])
            self.assertEqual(code, 0)
            line = next(
                line for line in errors.getvalue().splitlines()
                if line.startswith("workdir=")
            )
            workdir = Path(line.split("=", 1)[1])
            self.assertTrue(workdir.is_dir())
        finally:
            logging.disable(previous_disable)
            if workdir is not None:
                shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
