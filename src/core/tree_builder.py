# -*- coding: utf-8 -*-
"""TreeBuilder — построение базового дерева зависимостей файлов."""

from collections import defaultdict
from pathlib import Path

from .file_cache import FileCacheSystem
from .fs import (
    normalize_path,
    rel_path,
    read_text_safe,
    extract_includes,
    resolve_include_targets,
    scan_ltx_files,
    find_system_ltx,
    get_dltx_mods_for_root,
    describe_real_path,
)
from .models import LtxForest


class TreeBuilder:
    """
    Строит объективное базовое дерево зависимостей файлов.

    Важно:
    - не фильтрует дерево под UI;
    - не сортирует дерево под конкретную презентацию;
    - не разрешает финальные значения секций;
    - не генерирует патчи.
    """

    def __init__(self, cache=None):
        self.cache = cache or FileCacheSystem()

    def build(self, scan_dir, system_root=None, log=None, use_cache=True):
        """
        Построить дерево.

        Если system_root передан, scan_dir используется как есть.
        Если system_root не передан, ищем system.ltx сами.
        """
        log = log or (lambda m: None)

        if scan_dir is None:
            raise ValueError("scan_dir не задан")

        if system_root is not None:
            scan = normalize_path(scan_dir)
            system = normalize_path(system_root)
        else:
            sel = normalize_path(scan_dir)
            system = find_system_ltx(sel)

            if system is None:
                raise RuntimeError("system.ltx не найден в %s" % sel)

            if (sel / "system.ltx").is_file():
                scan = sel
            else:
                scan = system.parent

        key = ("tree_builder", str(scan), str(system))

        if use_cache:
            cached = self.cache.get(key)
            if cached is not None:
                return cached

        forest = self._build(scan, system, log)

        if use_cache:
            self.cache.set(key, forest)

        return forest

    def _build(self, scan_dir, system_root, log):
        scan_dir = normalize_path(scan_dir)
        system_root = normalize_path(system_root) if system_root else None

        files = scan_ltx_files(scan_dir)
        file_set = set(files)

        # Обе трактовки пути фиксируются ОДИН раз в момент сканирования:
        #   rel_paths  — игровой вид (VFS): идентичность, дерево, поиск;
        #   real_paths — физическое расположение (MO2-мод / GAME): атрибуция источников.
        game_root_real = scan_dir.resolve()
        rel_paths = {}
        real_paths = {}
        for f in files:
            rel_paths[f] = rel_path(f, scan_dir)
            real_paths[f] = describe_real_path(f.resolve(), game_root_real)

        by_folder = defaultdict(list)
        for f in files:
            by_folder[f.parent].append(f)

        log("Найдено .ltx файлов: %d" % len(files))

        raw_children = defaultdict(set)
        raw_parents = defaultdict(set)
        missing = []

        for parent in files:
            try:
                text = read_text_safe(parent)
            except Exception as e:
                log("Не удалось прочитать %s: %s" % (parent, e))
                continue

            for inc in extract_includes(text):
                targets = resolve_include_targets(inc, parent)

                if not targets:
                    missing.append((parent, inc))
                    continue

                for child in targets:
                    if child == parent or child not in file_set:
                        missing.append((parent, inc))
                        continue

                    raw_children[parent].add(child)
                    raw_parents[child].add(parent)

        log(
            "Include-связей: %d; пропавших: %d"
            % (
                sum(len(v) for v in raw_children.values()),
                len(missing),
            )
        )

        provisional = [f for f in files if not raw_parents.get(f)]

        dltx_children = defaultdict(set)
        attached = 0

        for root in provisional:
            folder_files = by_folder.get(root.parent, [])

            for mod in get_dltx_mods_for_root(root, folder_files):
                if mod != root and not raw_parents.get(mod):
                    raw_children[root].add(mod)
                    raw_parents[mod].add(root)
                    dltx_children[root].add(mod)
                    attached += 1

        log("DLTX mod_*.ltx присоединено к корням: %d" % attached)

        final_roots = [f for f in files if not raw_parents.get(f)]

        if system_root and system_root in file_set and system_root not in final_roots:
            final_roots.append(system_root)

        final_roots.sort(
            key=lambda p: (
                0 if p == system_root else 1,
                rel_paths.get(p, str(p)).lower(),
            )
        )

        display_children = defaultdict(list)
        node_parent = {}
        tree_root = {}
        tree_files = defaultdict(list)

        def assign(root):
            stack = [(root, None)]

            while stack:
                node, par = stack.pop()

                if node in tree_root:
                    continue

                tree_root[node] = root
                tree_files[root].append(node)

                if par is not None:
                    node_parent[node] = par
                    display_children[par].append(node)

                kids = sorted(
                    raw_children.get(node, set()),
                    key=lambda x: rel_paths.get(x, str(x)).lower(),
                    reverse=True,
                )

                for ch in kids:
                    if ch not in tree_root:
                        stack.append((ch, node))

        for r in final_roots:
            assign(r)

        # Циклы / сироты.
        for node in files:
            if node not in tree_root:
                final_roots.append(node)
                assign(node)

        for p in list(display_children.keys()):
            display_children[p].sort(
                key=lambda x: rel_paths.get(x, str(x)).lower()
            )

        actual_roots = [r for r in final_roots if tree_root.get(r) == r]

        actual_roots.sort(
            key=lambda x: (
                0 if x == system_root else 1,
                rel_paths.get(x, str(x)).lower(),
            )
        )

        subtree = {}

        for root in actual_roots:
            stack = [(root, False)]

            while stack:
                node, visited = stack.pop()

                if visited:
                    subtree[node] = 1 + sum(
                        subtree.get(c, 1)
                        for c in display_children.get(node, [])
                    )
                elif node not in subtree:
                    stack.append((node, True))

                    for c in display_children.get(node, []):
                        if c not in subtree:
                            stack.append((c, False))

        return LtxForest(
            scan_dir=str(scan_dir),
            system_root=str(system_root) if system_root else "",
            total_files=len(files),
            files=files,
            rel_paths=rel_paths,
            real_paths=real_paths,
            roots=actual_roots,
            roots_set=set(actual_roots),
            display_children=dict(display_children),
            node_parent=node_parent,
            subtree_counts=subtree,
            system_tree_files=(
                len(tree_files.get(system_root, [])) if system_root else 0
            ),
            include_children={k: set(v) for k, v in raw_children.items()},
            include_parents={k: set(v) for k, v in raw_parents.items()},
            dltx_attachments={k: set(v) for k, v in dltx_children.items()},
        )


# ------------------------------------------------------------ совместимость

def build_forest(scan_dir, system_root, log=None) -> LtxForest:
    """
    Совместимая функция со старым core.build_forest.

    Она сознательно не использует кэш по умолчанию,
    чтобы сохранить старое поведение.
    """
    builder = TreeBuilder()
    return builder.build(
        scan_dir=scan_dir,
        system_root=system_root,
        log=log,
        use_cache=False,
    )