# -*- coding: utf-8 -*-
"""Воспроизводимый дамп секций и сравнение с игрой/манифестом.

Только читает конфиги. Ничего не пишет в VFS игры, Overwrite или моды;
выходной файл создаётся только по явному ``--dump``/``--properties``.

Примеры:

    .venv\\Scripts\\python.exe tools\\semantic_parity.py ^
        --configs "H:\\GRA2.5_work\\Anomaly 1.5.3 up\\gamedata\\configs" ^
        --dump resolver_sections.txt

    .venv\\Scripts\\python.exe tools\\semantic_parity.py ^
        --configs "..." --compare game_sections.txt ^
        --compare-properties game_properties.txt ^
        --known baseline\\known-parity-differences.json ^
        --report parity-report.json

    .venv\\Scripts\\python.exe tools\\semantic_parity.py ^
        --configs "..." --manifest baseline\\stage0-manifest.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.cache.service import CacheService
from core.resolver.service import ResolverService
from core.semantic.parity import (
    compare_property_dumps,
    compare_section_dumps,
    dump_section_summary,
    load_known_differences,
    parse_manifest,
    parse_property_dump,
    parse_section_dump,
)
from core.semantic.query import SectionQueryService
from core.tools.cache import FileCacheSystem, ResolvedCache
from core.tree.builder import TreeBuilder


def _default_configs():
    env = os.environ.get("ACE_BASELINE_CONFIGS")
    if env:
        return Path(env)
    candidate = ROOT.parent / "Anomaly 1.5.3 up" / "gamedata" / "configs"
    return candidate


def _build_model(configs, root_path):
    configs = Path(configs)
    root_path = Path(root_path)
    cache = FileCacheSystem()
    forest = TreeBuilder(cache=cache).build(
        configs,
        root_path,
        use_cache=False,
    )
    service = ResolverService(
        cache,
        ResolvedCache(),
        cache_service=CacheService(resolved_cache=ResolvedCache()),
    )
    query = SectionQueryService(service)
    return forest, query.root(root_path, forest, configs)


def _write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


REPORT_FORMAT = "ace-parity-report-v1"


def _sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _result_payload(result):
    payload = asdict(result)
    payload["ok"] = result.ok
    return payload


def _build_report(*, index, configs, root_path, summary,
                  resolver_properties_text, results, game_info,
                  known, known_path, source_root, strict_row_order):
    known_ignored = set()
    for result in results.values():
        known_ignored.update(result.known_ignored)
    known_unused = sorted(set(known) - known_ignored)
    resolver = {
        "configs": str(configs),
        "root": str(root_path),
        "sections": len(index.names),
        "keys": sum(index.key_counts().values()),
        "summary_sha256": _sha256_text(summary),
    }
    if resolver_properties_text is not None:
        resolver["properties_sha256"] = _sha256_text(
            resolver_properties_text
        )
    known_payload = {
        "path": str(known_path) if known_path else None,
        "entries": sorted(known),
        "ignored": sorted(known_ignored),
        "unused": known_unused,
    }
    if known_path:
        known_payload["sha256"] = _sha256_file(known_path)
    results_payload = {
        name: _result_payload(result)
        for name, result in results.items()
    }
    modes = sorted(results)
    required_modes = {"properties", "summary"}
    missing_modes = sorted(required_modes - set(modes))
    complete = not missing_modes
    ok = all(result.ok for result in results.values()) and not known_unused
    return {
        "format": REPORT_FORMAT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ok": ok,
        "modes": modes,
        "complete": complete,
        "missing_modes": missing_modes,
        "release_gate_ready": complete and ok,
        "resolver": resolver,
        "game": game_info,
        "known": known_payload,
        "source_root": str(source_root) if source_root else None,
        "strict_row_order": bool(strict_row_order),
        "results": results_payload,
    }


def main(argv=None):
    # CLI не должен засорять stderr предупреждениями о медленном кэше.
    logging.disable(logging.WARNING)
    parser = argparse.ArgumentParser(
        description="Семантический дамп секций ACE",
    )
    parser.add_argument(
        "--configs",
        default=str(_default_configs()),
        help="каталог gamedata/configs",
    )
    parser.add_argument(
        "--root",
        default="",
        help="корневой LTX (по умолчанию system.ltx)",
    )
    parser.add_argument(
        "--dump",
        default="",
        help="записать section<TAB>key_count",
    )
    parser.add_argument(
        "--properties",
        default="",
        help="записать полный property-дамп (может быть большим)",
    )
    parser.add_argument(
        "--section",
        default="",
        help="ограничить --properties одной секцией",
    )
    parser.add_argument(
        "--compare",
        default="",
        help="сравнить с поимённым дампом игры",
    )
    parser.add_argument(
        "--compare-properties",
        default="",
        help=(
            "сравнить property-дамп игры: секции, ключи, значения "
            "и источники"
        ),
    )
    parser.add_argument(
        "--source-root",
        default="",
        help=(
            "корень для относительных путей file в property-дампе игры"
        ),
    )
    parser.add_argument(
        "--strict-row-order",
        action="store_true",
        help="проверять порядок повторяющихся строк одного ключа",
    )
    parser.add_argument(
        "--manifest",
        default="",
        help="сравнить с stage0-manifest.json",
    )
    parser.add_argument(
        "--known",
        default="",
        help="JSON-реестр известных отличий",
    )
    parser.add_argument(
        "--report",
        default="",
        help="записать JSON-отчёт сравнения",
    )
    parser.add_argument(
        "--release-gate",
        action="store_true",
        help=(
            "требовать --compare, --compare-properties и --report; "
            "успех только при release_gate_ready"
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="не печатать сводку",
    )
    args = parser.parse_args(argv)

    if args.release_gate:
        missing_flags = []
        if not args.compare:
            missing_flags.append("--compare")
        if not args.compare_properties:
            missing_flags.append("--compare-properties")
        if not args.report:
            missing_flags.append("--report")
        if missing_flags:
            parser.error(
                "--release-gate требует %s" % ", ".join(missing_flags)
            )

    configs = Path(args.configs)
    root_path = Path(args.root) if args.root else configs / "system.ltx"
    if not configs.is_dir():
        parser.error("каталог конфигов не найден: %s" % configs)
    if not root_path.is_file():
        parser.error("корневой LTX не найден: %s" % root_path)

    forest, root = _build_model(configs, root_path)
    index = root.sections
    summary = dump_section_summary(index)
    if not args.quiet:
        print(
            "sections=%d keys=%d diagnostics=%d"
            % (
                len(index.names),
                sum(index.key_counts().values()),
                len(root.diagnostics),
            )
        )

    if args.dump:
        path = _write(args.dump, summary)
        if not args.quiet:
            print("dump=%s" % path)

    resolver_properties_text = None
    if args.compare_properties:
        resolver_properties_text = index.dump()
    if args.properties:
        if args.section:
            properties_text = index.dump(section=args.section)
        elif resolver_properties_text is not None:
            properties_text = resolver_properties_text
        else:
            properties_text = index.dump()
        _write(args.properties, properties_text)
        if not args.quiet:
            print("properties=%s" % args.properties)

    known_path = Path(args.known) if args.known else None
    known = load_known_differences(known_path) if known_path else set()
    resolver_counts = parse_section_dump(summary)
    results = {}
    game_info = {}
    if args.compare:
        path = Path(args.compare)
        game_info["sections"] = {
            "path": str(path),
            "sha256": _sha256_file(path),
        }
        game = parse_section_dump(
            path.read_text(encoding="utf-8-sig")
        )
        results["summary"] = compare_section_dumps(
            resolver_counts,
            game,
            known=known,
        )
    if args.compare_properties:
        path = Path(args.compare_properties)
        game_info["properties"] = {
            "path": str(path),
            "sha256": _sha256_file(path),
        }
        game_properties = parse_property_dump(
            path.read_text(encoding="utf-8-sig"),
            source_root=args.source_root or None,
        )
        resolver_properties = parse_property_dump(
            resolver_properties_text,
        )
        results["properties"] = compare_property_dumps(
            resolver_properties,
            game_properties,
            known=known,
            compare_row_order=args.strict_row_order,
        )
    if args.manifest:
        path = Path(args.manifest)
        game_info["manifest"] = {
            "path": str(path),
            "sha256": _sha256_file(path),
        }
        manifest = parse_manifest(path)
        results["manifest"] = compare_section_dumps(
            resolver_counts,
            manifest,
            known=known,
        )

    if not results and args.report:
        parser.error(
            "--report требует --compare, --compare-properties "
            "или --manifest"
        )

    report_payload = None
    if args.report and results:
        report_payload = _build_report(
            index=index,
            configs=configs,
            root_path=root_path,
            summary=summary,
            resolver_properties_text=resolver_properties_text,
            results=results,
            game_info=game_info,
            known=known,
            known_path=known_path,
            source_root=args.source_root,
            strict_row_order=args.strict_row_order,
        )
        _write(
            args.report,
            json.dumps(
                report_payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ) + "\n",
        )
        if not args.quiet:
            print("report=%s" % args.report)

    if results:
        multiple = len(results) > 1
        for name, result in results.items():
            if multiple:
                print("[%s]" % name)
            print(result.as_text().rstrip())
        if report_payload is not None and not report_payload["ok"]:
            return 1
        if args.release_gate and not report_payload["release_gate_ready"]:
            return 1
        if any(not result.ok for result in results.values()):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
