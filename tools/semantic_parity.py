# -*- coding: utf-8 -*-
"""Воспроизводимый дамп секций и сравнение с игрой/манифестом.

Только читает конфиги. Ничего не пишет в VFS игры, Overwrite или моды;
выходной файл создаётся только по явному ``--dump``/``--properties``.

Примеры:

    .venv\\Scripts\\python.exe tools\\semantic_parity.py ^
        --configs "H:\\GRA2.5_work\\Anomaly 1.5.3 up\\gamedata\\configs" ^
        --dump resolver_sections.txt

    .venv\\Scripts\\python.exe tools\\semantic_parity.py ^
        --configs "..." --compare game_sections.txt

    .venv\\Scripts\\python.exe tools\\semantic_parity.py ^
        --configs "..." --manifest docs\\baseline\\stage0-manifest.json
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.cache.service import CacheService
from core.resolver.service import ResolverService
from core.semantic.parity import (
    compare_section_dumps,
    dump_section_summary,
    load_known_differences,
    parse_manifest,
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
        "--quiet",
        action="store_true",
        help="не печатать сводку",
    )
    args = parser.parse_args(argv)

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
    if args.properties:
        _write(
            args.properties,
            index.dump(section=args.section or None),
        )
        if not args.quiet:
            print("properties=%s" % args.properties)

    known = load_known_differences(args.known) if args.known else set()
    resolver_counts = parse_section_dump(summary)
    result = None
    if args.compare:
        game = parse_section_dump(
            Path(args.compare).read_text(encoding="utf-8")
        )
        result = compare_section_dumps(
            resolver_counts,
            game,
            known=known,
        )
    elif args.manifest:
        manifest = parse_manifest(args.manifest)
        result = compare_section_dumps(
            resolver_counts,
            manifest,
            known=known,
        )

    if result is not None:
        print(result.as_text().rstrip())
        if not result.ok:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
