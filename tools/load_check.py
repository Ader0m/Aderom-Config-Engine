# -*- coding: utf-8 -*-
"""Нагрузочная проверка release gate на синтетическом VFS.

Создаёт временный ``gamedata/configs`` с 20k+ файлов и 19k+ секций,
строит дерево, собирает resolved-текст, измеряет память, параллельную
предсборку и её отмену. Оригиналы модов, ``Overwrite`` и VFS игры не
затрагиваются: всё пишется только во временный каталог.

Пример:

    .venv\\Scripts\\python.exe tools\\load_check.py ^
        --json "$env:TEMP\\load-check.json"
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.patching.ltx.document import declared_sections
from core.resolver.prebuild import PrebuildService
from core.resolver.service import ResolverService
from core.tools.cache import FileCacheSystem, ResolvedCache
from core.tree.builder import TreeBuilder

FORMAT = "ace-load-check-v1"


def _process_memory_mb():
    """RSS и пик RSS процесса (Windows); иначе пустой словарь."""
    if os.name != "nt":
        return {}
    try:
        import ctypes.wintypes as wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        process = kernel32.GetCurrentProcess()
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        get_info = kernel32.K32GetProcessMemoryInfo
        get_info.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        if not get_info(process, ctypes.byref(counters), counters.cb):
            return {}
        return {
            "rss_mb": round(counters.WorkingSetSize / 1024 / 1024, 1),
            "peak_rss_mb": round(
                counters.PeakWorkingSetSize / 1024 / 1024,
                1,
            ),
        }
    except Exception:
        return {}


def generate_synthetic_vfs(config_dir, sections=19000, files=20000):
    """Создать VFS: один корень на ``sections`` секций и отдельные roots.

    ``files`` — общее число LTX-файлов. Один ``system.ltx`` включает
    ``big.ltx``, который включает ``sections`` файлов; остаток файлов —
    отдельные корни для параллельной предсборки.
    """
    config_dir = Path(config_dir)
    sections = int(sections)
    files = int(files)
    if sections < 1:
        raise ValueError("sections должен быть >= 1")
    if files < sections + 2:
        raise ValueError("files должен быть >= sections + 2")
    section_dir = config_dir / "sections"
    standalone_dir = config_dir / "standalone"
    section_dir.mkdir(parents=True, exist_ok=True)
    standalone_dir.mkdir(parents=True, exist_ok=True)

    for index in range(sections):
        (section_dir / ("sec_%05d.ltx" % index)).write_text(
            "[sec_%05d]\nvalue = %d\n" % (index, index),
            encoding="cp1251",
        )

    big = config_dir / "big.ltx"
    big.write_text(
        "[big]\n"
        + "".join(
            '#include "sections/sec_%05d.ltx"\n' % index
            for index in range(sections)
        ),
        encoding="cp1251",
    )
    system = config_dir / "system.ltx"
    system.write_text(
        '[system]\n#include "big.ltx"\n',
        encoding="cp1251",
    )

    extra = files - sections - 2
    for index in range(extra):
        (standalone_dir / ("root_%05d.ltx" % index)).write_text(
            "[root_%05d]\nvalue = %d\n" % (index, index),
            encoding="cp1251",
        )
    return {
        "configs": config_dir,
        "system": system,
        "section_files": sections,
        "standalone_roots": extra,
        "files": sections + 2 + extra,
        "sections": sections + 2 + extra,
    }


def _mem_marker(memory, name):
    memory[name] = _process_memory_mb()


def _timed(fn):
    started = time.perf_counter()
    value = fn()
    return value, (time.perf_counter() - started) * 1000.0


def _wait_prebuild(prebuild, timeout, stop_after=None):
    started = time.perf_counter()
    stopped = False
    while True:
        status = prebuild.status()
        elapsed = (time.perf_counter() - started) * 1000.0
        if stop_after is not None and not stopped and (
            status["state"] == "running"
        ) and elapsed >= stop_after:
            prebuild.stop()
            stopped = True
        if status["state"] != "running":
            break
        if elapsed >= float(timeout) * 1000.0:
            prebuild.stop()
            stopped = True
            status = prebuild.status()
            break
        time.sleep(0.05)
    prebuild.wait(timeout=5)
    return {
        "elapsed_ms": round(elapsed, 1),
        "state": prebuild.status()["state"],
        "done": prebuild.status()["done"],
        "total": prebuild.status()["total"],
        "error": prebuild.status()["error"],
        "stopped": stopped,
    }


def evaluate_ok(counts, prebuild_result, cancel_result):
    """Строгий критерий успешной нагрузочной проверки."""
    prebuild_done = prebuild_result.get("done")
    prebuild_total = prebuild_result.get("total")
    cancel_done = cancel_result.get("done")
    cancel_total = cancel_result.get("total")
    return bool(
        counts.get("files", 0) >= 20000
        and counts.get("resolved_sections", 0) >= 19000
        and counts.get("roots", 0) >= 999
        and prebuild_result.get("state") == "done"
        and prebuild_result.get("error") is None
        and prebuild_total is not None
        and prebuild_total >= 999
        and prebuild_done == prebuild_total
        and cancel_result.get("stopped") is True
        and cancel_result.get("error") is None
        and cancel_result.get("state") in ("idle", "done")
        and cancel_total is not None
        and cancel_total > 0
        and cancel_done is not None
        and cancel_done < cancel_total
    )


def run_check(config_dir, system, workers=4, timeout=120):
    config_dir = Path(config_dir)
    system = Path(system)
    memory = {"start": _process_memory_mb()}

    cache = FileCacheSystem()
    tree_builder = TreeBuilder(cache=cache)
    forest, tree_ms = _timed(
        lambda: tree_builder.build(
            config_dir,
            system,
            use_cache=False,
        )
    )
    _mem_marker(memory, "after_tree")

    resolver = ResolverService(cache, ResolvedCache())
    resolved, resolve_ms = _timed(
        lambda: resolver.assemble_resolved(
            system,
            forest,
            config_dir,
        )
    )
    text, _sources = resolved
    _mem_marker(memory, "after_resolve")
    sections = len(declared_sections(text))

    prebuild_cache = FileCacheSystem()
    prebuild_resolver = ResolverService(
        prebuild_cache,
        ResolvedCache(),
    )
    prebuild = PrebuildService(prebuild_resolver, workers=workers)
    prebuild.start(forest, config_dir)
    prebuild_result = _wait_prebuild(prebuild, timeout=timeout)
    _mem_marker(memory, "after_prebuild")

    cancel_cache = FileCacheSystem()
    cancel_resolver = ResolverService(
        cancel_cache,
        ResolvedCache(),
    )
    cancel = PrebuildService(cancel_resolver, workers=workers)
    cancel.start(forest, config_dir)
    cancel_result = _wait_prebuild(
        cancel,
        timeout=timeout,
        stop_after=50,
    )
    _mem_marker(memory, "after_cancel")

    counts = {
        "files": len(forest.files),
        "roots": len(forest.roots),
        "resolved_sections": sections,
        "resolved_chars": len(text),
    }
    ok = evaluate_ok(counts, prebuild_result, cancel_result)
    return {
        "format": FORMAT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "parameters": {
            "configs": str(config_dir),
            "system": str(system),
            "workers": int(workers),
            "timeout": int(timeout),
        },
        "counts": counts,
        "memory": memory,
        "timings_ms": {
            "tree_build": round(tree_ms, 1),
            "resolve_system": round(resolve_ms, 1),
            "prebuild_parallel": prebuild_result["elapsed_ms"],
            "prebuild_cancel": cancel_result["elapsed_ms"],
        },
        "prebuild": prebuild_result,
        "cancel": cancel_result,
        "ok": bool(ok),
    }


def main(argv=None):
    logging.disable(logging.WARNING)
    parser = argparse.ArgumentParser(
        description="Нагрузочная проверка ACE на синтетическом VFS",
    )
    parser.add_argument("--sections", type=int, default=19000)
    parser.add_argument("--files", type=int, default=20000)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--workdir", default="")
    parser.add_argument("--json", default="")
    parser.add_argument("--skip-prebuild", action="store_true")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)

    if args.workdir:
        workdir = Path(args.workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        temporary = None
    elif args.keep:
        workdir = Path(tempfile.mkdtemp(prefix="ace-load-check-"))
        temporary = None
    else:
        temporary = tempfile.TemporaryDirectory()
        workdir = Path(temporary.name)
    try:
        configs = workdir / "gamedata" / "configs"
        generated = generate_synthetic_vfs(
            configs,
            sections=args.sections,
            files=args.files,
        )
        if args.skip_prebuild:
            report = {
                "format": FORMAT,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "parameters": {
                    "sections": args.sections,
                    "files": args.files,
                },
                "counts": {
                    "files": generated["files"],
                    "sections": generated["sections"],
                },
                "ok": True,
            }
        else:
            report = run_check(
                configs,
                generated["system"],
                workers=args.workers,
                timeout=args.timeout,
            )
        text = json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        print(text, end="")
        if args.json:
            path = Path(args.json)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return 0 if report["ok"] else 1
    finally:
        if temporary is not None:
            temporary.cleanup()
        if args.keep and not args.workdir:
            print("workdir=%s" % workdir, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
