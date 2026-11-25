# -*- coding: utf-8 -*-
"""Воспроизводимый performance baseline Этапа 0.

Скрипт только читает конфиги игры и создаёт временный проект. Оригиналы
модов, Overwrite и VFS-каталог игры не изменяются.

Пример:

    .venv\\Scripts\\python.exe tools\\perf_baseline.py ^
        --configs "H:\\GRA2.5_work\\Anomaly 1.5.3 up\\gamedata\\configs"

Путь можно задать через ``ACE_BASELINE_CONFIGS``. По умолчанию скрипт
ищет соседнюю установку ``Anomaly 1.5.3 up`` рядом с репозиторием.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _process_memory_mb():
    """Рабочий набор и пик процесса (Windows); иначе пустой словарь."""
    if os.name != "nt":
        return {}
    try:
        import ctypes
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


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path, scan):
    path = Path(path)
    try:
        return path.resolve().relative_to(scan.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _system_tree_files(forest, root, scan):
    children = getattr(forest, "display_children", {}) or {}
    seen = set()
    stack = [Path(root)]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(children.get(node, ()))
    return sorted(_relative(path, scan) for path in seen)


def _build_manifest(scan, forest, text, document):
    """Эталонный набор секций/файлов с воспроизводимой подписью."""
    system_root = Path(forest.system_root)
    sections = {
        str(name): len(section.entries)
        for name, section in document.sections.items()
    }
    sections = dict(sorted(sections.items()))

    payload = {
        "format": "ace-baseline-manifest-v1",
        "source_system_ltx": _relative(system_root, scan),
        "hashes": {
            "system_ltx_sha256": _sha256_file(system_root),
            "resolved_sha256": _sha256_bytes(text.encode("utf-8")),
        },
        "counts": {
            "sections": len(sections),
            "keys": sum(sections.values()),
            "ltx_files": len(forest.files),
            "xml_files": len(forest.xml_files),
            "roots": len(forest.roots),
            "system_tree_files": len(
                _system_tree_files(forest, system_root, scan)
            ),
        },
        "sections": sections,
        "ltx_files": sorted(
            _relative(path, scan) for path in forest.files
        ),
        "xml_files": sorted(
            _relative(path, scan) for path in forest.xml_files
        ),
        "roots": sorted(
            _relative(path, scan) for path in forest.roots
        ),
        "system_tree_files": _system_tree_files(
            forest,
            system_root,
            scan,
        ),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    manifest = dict(payload)
    manifest["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    manifest["signature"] = _sha256_bytes(canonical.encode("utf-8"))
    return manifest


def _find_configs(explicit=None):
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    env_path = os.environ.get("ACE_BASELINE_CONFIGS")
    if env_path:
        candidates.append(Path(env_path))
    candidates.extend(
        [
            ROOT.parent / "Anomaly 1.5.3 up" / "gamedata" / "configs",
            ROOT.parent / "Anomaly 1.5.3 up" / "gamedata",
            ROOT.parent / "Anomaly 1.5.3 up",
        ]
    )
    for candidate in candidates:
        if (candidate / "system.ltx").is_file():
            return candidate.resolve()
    return None


def _timed(fn):
    started = time.perf_counter()
    value = fn()
    return value, (time.perf_counter() - started) * 1000.0


def _make_services(
    cache,
    resolver,
    generated_dir,
    cache_service=None,
    snapshot_service=None,
    resolved_cache=None,
):
    from core.facade import CoreServices
    from core.resolver.assembly import AssemblyService
    from core.tree.builder import TreeBuilder
    from core.tree.graph import ReferenceGraphBuilder
    from core.tree.query import TreeQueryService
    from core.xml.service import XmlResolveService

    return CoreServices(
        cache=cache,
        tree_builder=TreeBuilder(
            cache=cache,
            cache_service=cache_service,
        ),
        tree_query=TreeQueryService(),
        graph_builder=ReferenceGraphBuilder(
            cache_service=cache_service,
        ),
        resolver_service=resolver,
        assembly_service=AssemblyService(resolver, generated_dir),
        generated_dir=generated_dir,
        xml_resolve_service=XmlResolveService(
            resolved_cache=resolved_cache,
            cache_service=cache_service,
        ),
        cache_service=cache_service,
        snapshot_service=snapshot_service,
    )


def _change_source_text(text):
    """Внести детерминированную правку в LTX-текст."""
    changed, count = re.subn(
        r"(?m)^(\s*[A-Za-z_][\w]*\s*=\s*)(-?\d+(?:\.\d+)?)(\s*)$",
        r"\g<1>2\3",
        text,
        count=1,
    )
    if count:
        return changed
    return text + "\n[ace_baseline_probe]\nvalue = 1\n"


def _measure_preview(
    scan,
    forest,
    cache,
    resolver,
    cache_service=None,
    snapshot_service=None,
    resolved_cache=None,
):
    from app.projects import ProjectService

    with tempfile.TemporaryDirectory() as temp:
        temp_dir = Path(temp)
        services = _make_services(
            cache,
            resolver,
            temp_dir / "generated",
            cache_service=cache_service,
            snapshot_service=snapshot_service,
            resolved_cache=resolved_cache,
        )
        project = ProjectService(temp_dir / "projects")
        project.create("Baseline", save_mode="root")

        source = scan / "creatures" / "m_stalker.ltx"
        if not source.is_file():
            source = next(
                (path for path in forest.files if path.suffix == ".ltx"),
                None,
            )
        if source is None:
            return {"available": False, "reason": "нет LTX-файлов"}

        text = source.read_text(encoding="windows-1251", errors="replace")
        project.update_draft(source, _change_source_text(text))
        preview, elapsed = _timed(
            lambda: project.build_preview(services, forest)
        )
        return {
            "available": True,
            "source": str(source),
            "elapsed_ms": round(elapsed, 1),
            "items": len(preview.items),
            "ok_items": sum(1 for item in preview.items if item.ok),
        }


def _measure_disk_cache(scan, forest):
    """Холодное и тёплое открытие resolved-корня через дисковый кэш."""
    from core.cache.service import CacheService
    from core.resolver.service import ResolverService
    from core.tools.cache import FileCacheSystem, ResolvedCache

    with tempfile.TemporaryDirectory() as temp:
        resolved_cache = ResolvedCache(cache_dir=Path(temp))
        cache_service = CacheService(resolved_cache=resolved_cache)
        resolver = ResolverService(
            FileCacheSystem(),
            resolved_cache,
            cache_service=cache_service,
        )
        resolver.ensure_adapter(forest, scan)
        root = Path(forest.system_root)
        text, cold = _timed(
            lambda: resolver.assemble_text(root, forest, scan)
        )
        _text, warm = _timed(
            lambda: resolver.assemble_text(root, forest, scan)
        )
        return {
            "cold_ms": round(cold, 1),
            "warm_ms": round(warm, 1),
            "chars": len(text),
        }


def _gap_stats(ticks):
    if len(ticks) < 2:
        return {"available": False, "reason": "нет тиков QTimer"}
    gaps = [
        (ticks[index] - ticks[index - 1]) * 1000.0
        for index in range(1, len(ticks))
    ]
    return {
        "available": True,
        "ticks": len(ticks),
        "max_gap_ms": round(max(gaps), 1),
        "p95_gap_ms": round(
            sorted(gaps)[int(len(gaps) * 0.95) - 1],
            1,
        ),
    }


def _measure_ui(scan):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication

    from app.workspace import SnapshotService
    from core.cache.service import CacheService
    from core.resolver.prebuild import PrebuildService
    from core.resolver.service import ResolverService
    from core.tools.cache import FileCacheSystem, ResolvedCache
    from core.tree.builder import TreeBuilder
    from ui.tasks import TaskService

    app = QApplication.instance() or QApplication([])
    result = {}

    # 1) Построение дерева через платформенный TaskService.
    tasks = TaskService()
    ticks = []
    timer = QTimer()
    timer.setInterval(16)
    state = {"running": True, "started": time.perf_counter(), "elapsed": 0.0}

    def tick():
        if state["running"]:
            ticks.append(time.perf_counter())

    def done(_result, _error):
        state["elapsed"] = (time.perf_counter() - state["started"]) * 1000.0
        state["running"] = False
        timer.stop()
        app.quit()

    ui_cache_service = CacheService()
    ui_snapshot_service = SnapshotService()

    def build_ui_forest():
        forest = TreeBuilder(
            cache=FileCacheSystem(),
            cache_service=ui_cache_service,
        ).build(scan, use_cache=False)
        snapshot = ui_snapshot_service.observe_forest(forest)
        forest.cache_id = snapshot.id
        forest.overlay_revision = snapshot.overlay_revision
        return forest

    timer.timeout.connect(tick)
    timer.start()
    tasks.run(
        lambda _log: build_ui_forest(),
        on_done=done,
    )
    app.exec_()
    tasks.shutdown()
    result["tree_task"] = {
        "elapsed_ms": round(state["elapsed"], 1),
        **_gap_stats(ticks),
    }

    # 2) Фоновая предсборка одного корня через PrebuildService.
    forest = build_ui_forest()
    resolver = ResolverService(
        FileCacheSystem(),
        ResolvedCache(),
        cache_service=ui_cache_service,
    )
    resolver.ensure_adapter(forest, scan)
    prebuild = PrebuildService(resolver, workers=1)

    ticks = []
    timer = QTimer()
    timer.setInterval(16)
    poll = QTimer()
    poll.setInterval(50)
    timeout = QTimer()
    timeout.setSingleShot(True)
    state = {
        "running": True,
        "started": time.perf_counter(),
        "elapsed": 0.0,
        "status": {},
        "timed_out": False,
    }

    def tick_prebuild():
        if state["running"]:
            ticks.append(time.perf_counter())

    def finish_prebuild():
        state["elapsed"] = (time.perf_counter() - state["started"]) * 1000.0
        state["running"] = False
        state["status"] = prebuild.status()
        timer.stop()
        poll.stop()
        timeout.stop()
        app.quit()

    def check_prebuild():
        status = prebuild.status()
        if status["state"] == "done" or (
            status["state"] == "idle" and not status["pending"]
        ):
            finish_prebuild()

    def timeout_prebuild():
        state["timed_out"] = True
        finish_prebuild()

    timer.timeout.connect(tick_prebuild)
    poll.timeout.connect(check_prebuild)
    timeout.timeout.connect(timeout_prebuild)
    timer.start()
    prebuild.start(
        forest,
        scan,
        roots=[Path(forest.system_root)],
    )
    poll.start()
    timeout.start(120_000)
    app.exec_()
    prebuild.stop()
    prebuild.wait(timeout=5)
    result["prebuild"] = {
        "elapsed_ms": round(state["elapsed"], 1),
        "timed_out": state["timed_out"],
        "error": state["status"].get("error"),
        **_gap_stats(ticks),
    }
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--configs",
        help="каталог gamedata/configs с system.ltx",
    )
    parser.add_argument(
        "--json",
        help="куда сохранить JSON (по умолчанию только stdout)",
    )
    parser.add_argument(
        "--manifest",
        help="куда сохранить эталонный набор секций/файлов",
    )
    parser.add_argument(
        "--skip-ui",
        action="store_true",
        help="не измерять отзывчивость QTimer",
    )
    args = parser.parse_args(argv)

    scan = _find_configs(args.configs)
    if scan is None:
        parser.error(
            "не найден system.ltx; укажите --configs или "
            "ACE_BASELINE_CONFIGS"
        )

    from core.patching.ltx.document import (
        declared_sections,
        parse_ltx_document,
    )
    from app.workspace import SnapshotService
    from core.cache.service import CacheService
    from core.resolver.service import ResolverService
    from core.tools.cache import FileCacheSystem, ResolvedCache
    from core.tree.builder import TreeBuilder

    memory = {"start": _process_memory_mb()}
    cache = FileCacheSystem()
    resolved_cache = ResolvedCache()
    cache_service = CacheService(resolved_cache=resolved_cache)
    snapshot_service = SnapshotService()
    tree_builder = TreeBuilder(
        cache=cache,
        cache_service=cache_service,
    )

    tree_parts = {}

    def build_tree_production():
        started = time.perf_counter()
        forest = tree_builder.build(scan, use_cache=False)
        tree_parts["raw_ms"] = (time.perf_counter() - started) * 1000.0
        started = time.perf_counter()
        snapshot = snapshot_service.observe_forest(forest)
        tree_parts["snapshot_ms"] = (time.perf_counter() - started) * 1000.0
        forest.cache_id = snapshot.id
        forest.overlay_revision = snapshot.overlay_revision
        return forest

    forest, tree_ms = _timed(build_tree_production)
    memory["after_tree"] = _process_memory_mb()

    resolver = ResolverService(
        cache,
        resolved_cache,
        cache_service=cache_service,
    )
    resolver.ensure_adapter(forest, scan)
    _sections, resolver_ms = _timed(
        lambda: resolver.run_pass(forest, scan)
    )
    memory["after_resolver"] = _process_memory_mb()

    text, assemble_ms = _timed(
        lambda: resolver.assemble_text(
            Path(forest.system_root),
            forest,
            scan,
        )
    )
    memory["after_assemble"] = _process_memory_mb()

    disk_cache = _measure_disk_cache(scan, forest)
    memory["after_disk_cache"] = _process_memory_mb()

    section_names = declared_sections(text)
    target = "stalker" if "stalker" in section_names else (
        section_names[0] if section_names else ""
    )
    document, parse_ms = _timed(lambda: parse_ltx_document(text))
    section, lookup_ms = _timed(lambda: document.section(target))
    memory["after_section"] = _process_memory_mb()

    manifest = None
    if args.manifest:
        manifest = _build_manifest(scan, forest, text, document)
        target_path = Path(args.manifest)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    preview = _measure_preview(
        scan,
        forest,
        cache,
        resolver,
        cache_service=cache_service,
        snapshot_service=snapshot_service,
        resolved_cache=resolved_cache,
    )
    memory["after_preview"] = _process_memory_mb()

    payload = {
        "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "composition": "stage2: CacheService + SnapshotService",
        "python": sys.version.split()[0],
        "configs": str(scan),
        "counts": {
            "ltx": forest.total_files,
            "xml": forest.total_xml_files,
            "roots": len(forest.roots),
            "system_tree_files": forest.system_tree_files,
            "resolver_sections": forest.resolver_sections,
            "resolved_chars": len(text),
            "declared_sections": len(section_names),
        },
        "timings_ms": {
            "tree_build": round(tree_ms, 1),
            "tree_build_raw": round(
                tree_parts.get("raw_ms", 0.0),
                1,
            ),
            "snapshot_observe": round(
                tree_parts.get("snapshot_ms", 0.0),
                1,
            ),
            "resolver_pass": round(resolver_ms, 1),
            "assemble_system": round(assemble_ms, 1),
            "assemble_disk_cold": disk_cache["cold_ms"],
            "assemble_disk_warm": disk_cache["warm_ms"],
            "section_parse": round(parse_ms, 1),
            "section_lookup": round(lookup_ms, 1),
            "patch_preview": preview.pop("elapsed_ms", None),
        },
        "disk_cache": disk_cache,
        "section": {
            "name": target,
            "found": section is not None,
        },
        "manifest": (
            {
                "path": args.manifest,
                "signature": manifest["signature"],
                "sections": manifest["counts"]["sections"],
                "keys": manifest["counts"]["keys"],
            }
            if manifest is not None
            else None
        ),
        "preview": preview,
        "memory_mb": memory,
        "ui": (
            {"available": False, "reason": "skipped"}
            if args.skip_ui
            else _measure_ui(scan)
        ),
    }

    output = json.dumps(payload, ensure_ascii=False, indent=2)
    print(output)
    if args.json:
        target = Path(args.json)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(output + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
