# -*- coding: utf-8 -*-
"""TreeBuilder — построение базового дерева зависимостей файлов."""

from collections import defaultdict
from pathlib import Path

from core.models import LtxForest
from core.resolver.attribution import describe_real_path
from core.tools.cache import FileCacheSystem, overlay_content_signature
from core.tools.fs import (
    normalize_path,
    rel_path,
    read_text_safe,
    scan_files,
    find_system_ltx,
)
from core.tree.dltx_model import get_dltx_mods_for_root
from core.tree.include_model import extract_includes, resolve_include_targets


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

    def build(
        self,
        scan_dir,
        system_root=None,
        log=None,
        use_cache=True,
        overlay=None,
    ):
        """
        Построить дерево.

        Если system_root передан, scan_dir используется как есть.
        Если system_root не передан, ищем system.ltx сами.

        ``overlay`` — карта ``{абсолютный путь: текст}`` с черновиками
        проекта. Новые файлы добавляются в дерево, а содержимое
        существующих читается из overlay, а не с диска.
        """
        log = log or (lambda m: None)

        if scan_dir is None:
            raise ValueError("scan_dir не задан")

        overlay = self._normalize_overlay(overlay)

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

        key = (
            "tree_builder",
            str(scan),
            str(system),
            overlay_content_signature(overlay),
        )

        if use_cache:
            cached = self.cache.get(key)
            if cached is not None:
                return cached

        forest = self._build(scan, system, log, overlay)

        if use_cache:
            self.cache.set(key, forest)

        return forest

    @staticmethod
    def _normalize_overlay(overlay):
        source_files = getattr(overlay, "source_files", None)
        if source_files is None:
            source_files = overlay or {}

        result = {}
        for path, text in source_files.items():
            try:
                result[normalize_path(path)] = str(text or "")
            except (TypeError, ValueError, OSError):
                continue
        return result

    def _build(self, scan_dir, system_root, log, overlay=None):
        scan_dir = normalize_path(scan_dir)
        system_root = normalize_path(system_root) if system_root else None
        overlay = overlay or {}

        # Один обход ФС на оба типа: LTX — дерево зависимостей,
        # XML — независимые файлы для просмотра без резолва.
        tree_files = scan_files(scan_dir, {".ltx", ".xml"})
        known = set(tree_files)

        for path in overlay:
            if path in known:
                continue
            if path.suffix.lower() not in (".ltx", ".xml"):
                continue
            try:
                path.relative_to(scan_dir)
            except ValueError:
                continue
            tree_files.append(path)

        tree_files.sort(key=lambda path: str(path).lower())
        files = [f for f in tree_files if f.suffix.lower() == ".ltx"]
        xml_files = [f for f in tree_files if f.suffix.lower() == ".xml"]
        file_set = set(files)

        # Обе трактовки пути фиксируются ОДИН раз в момент сканирования:
        #   rel_paths  — игровой вид (VFS): идентичность, дерево, поиск;
        #   real_paths — физическое расположение (MO2-мод / GAME): атрибуция источников.
        game_root_real = scan_dir.resolve()
        rel_paths = {}
        real_paths = {}
        for f in files + xml_files:
            rel_paths[f] = rel_path(f, scan_dir)
            real_paths[f] = describe_real_path(f.resolve(), game_root_real)

        by_folder = defaultdict(list)
        for f in files:
            by_folder[f.parent].append(f)

        log("Найдено .ltx файлов: %d" % len(files))
        log("Найдено .xml файлов: %d" % len(xml_files))

        raw_children = defaultdict(set)
        raw_parents = defaultdict(set)
        missing = []

        for parent in files:
            try:
                text = overlay.get(parent)
                if text is None:
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

        # Виртуальные mod_*.ltx проекта могут относиться к файлу, который
        # сам подключён через #include. Прикрепляем их к такому файлу,
        # чтобы созданный черновик был виден в дереве.
        virtual_ltx = {
            path for path in overlay
            if path.suffix.lower() == ".ltx"
            and path not in known
        }
        if virtual_ltx:
            for target in files:
                if target in virtual_ltx:
                    continue
                folder_files = by_folder.get(target.parent, [])
                for mod in get_dltx_mods_for_root(target, folder_files):
                    if mod not in virtual_ltx or raw_parents.get(mod):
                        continue
                    if mod == target:
                        continue
                    raw_children[target].add(mod)
                    raw_parents[mod].add(target)
                    dltx_children[target].add(mod)
                    attached += 1

        log("DLTX mod_*.ltx присоединено к корням: %d" % attached)

        final_roots = [f for f in files if not raw_parents.get(f)]

        if system_root and system_root in file_set and system_root not in final_roots:
            final_roots.append(system_root)

        # XML — независимые корни. У них нет #include и DLTX-патчей,
        # они не проходят через резолвер: каждый файл сам себе корень.
        final_roots.extend(xml_files)

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
            xml_files=xml_files,
            total_xml_files=len(xml_files),
        )


def _sort_roots(forest):
    system_root = Path(forest.system_root) if forest.system_root else None
    forest.roots.sort(
        key=lambda path: (
            0 if system_root is not None and path == system_root else 1,
            forest.rel_paths.get(path, str(path)).lower(),
        )
    )
    forest.roots_set = set(forest.roots)


def _recount_subtrees(forest):
    children = forest.display_children or {}
    counts = {}
    visiting = set()

    def calc(node):
        if node in counts:
            return counts[node]
        if node in visiting:
            return 1

        visiting.add(node)
        total = 1
        for child in children.get(node, ()):
            total += calc(child)
        visiting.discard(node)
        counts[node] = total
        return total

    for node in list(forest.files) + list(forest.xml_files):
        calc(node)

    system_root = Path(forest.system_root) if forest.system_root else None
    forest.subtree_counts = counts
    if system_root is not None:
        forest.system_tree_files = counts.get(system_root, 0)


def _invalidate_derived_indexes(forest) -> None:
    """Сбросить индексы целей патча после изменения состава леса."""
    try:
        from core.patching.targets import invalidate_forest_indexes
    except Exception:
        return
    invalidate_forest_indexes(forest)


def add_virtual_file(forest, path, text) -> bool:
    """Добавить новый виртуальный файл в уже построенное дерево."""
    path = normalize_path(path)
    scan_dir = normalize_path(forest.scan_dir)
    suffix = path.suffix.lower()

    if suffix not in (".ltx", ".xml"):
        return False
    if path in forest.files or path in forest.xml_files:
        return False

    try:
        relative = path.relative_to(scan_dir)
    except ValueError:
        return False

    forest.rel_paths[path] = rel_path(path, scan_dir)
    try:
        forest.real_paths[path] = describe_real_path(
            path.resolve(),
            scan_dir.resolve(),
        )
    except OSError:
        forest.real_paths[path] = str(path)

    if suffix == ".xml":
        forest.xml_files.append(path)
        forest.total_xml_files = len(forest.xml_files)
        forest.display_children.setdefault(path, [])
        forest.roots.append(path)
        _sort_roots(forest)
        _recount_subtrees(forest)
        _invalidate_derived_indexes(forest)
        return True

    forest.files.append(path)
    forest.total_files = len(forest.files)
    forest.display_children.setdefault(path, [])
    file_set = set(forest.files)

    for include in extract_includes(str(text or "")):
        for child in resolve_include_targets(include, path):
            if child == path or child not in file_set:
                continue
            forest.include_children.setdefault(path, set()).add(child)
            forest.include_parents.setdefault(child, set()).add(path)
            forest.display_children.setdefault(path, []).append(child)
            forest.node_parent[child] = path
            if child in forest.roots_set:
                forest.roots = [item for item in forest.roots if item != child]

    parent = None
    folder_files = [
        item for item in file_set
        if item.parent == path.parent and item != path
    ]
    folder_files.append(path)
    for target in sorted(folder_files, key=lambda item: item.name.lower()):
        if target == path:
            continue
        if path in get_dltx_mods_for_root(target, folder_files):
            parent = target
            break

    if parent is not None:
        forest.dltx_attachments.setdefault(parent, set()).add(path)
        forest.include_children.setdefault(parent, set()).add(path)
        forest.include_parents.setdefault(path, set()).add(parent)
        forest.display_children.setdefault(parent, []).append(path)
        forest.node_parent[path] = parent
    else:
        forest.roots.append(path)

    for key in (parent, path):
        if key is None:
            continue
        forest.display_children[key].sort(
            key=lambda item: forest.rel_paths.get(
                item, str(item)
            ).lower()
        )

    _sort_roots(forest)
    _recount_subtrees(forest)
    _invalidate_derived_indexes(forest)
    return True


def remove_virtual_file(forest, path) -> bool:
    """Убрать виртуальный файл из построенного дерева."""
    path = normalize_path(path)

    in_ltx = path in forest.files
    in_xml = path in forest.xml_files
    if not in_ltx and not in_xml:
        return False

    parent = forest.node_parent.pop(path, None)
    if parent is not None:
        forest.display_children[parent] = [
            item for item in forest.display_children.get(parent, ())
            if item != path
        ]
        forest.include_children.get(parent, set()).discard(path)
        forest.include_parents.get(path, set()).discard(parent)
        forest.dltx_attachments.get(parent, set()).discard(path)

    for child in list(forest.include_children.get(path, ())):
        forest.include_parents.get(child, set()).discard(path)
        if not forest.include_parents.get(child):
            forest.roots.append(child)

    forest.include_children.pop(path, None)
    forest.include_parents.pop(path, None)
    forest.dltx_attachments.pop(path, None)
    forest.display_children.pop(path, None)
    forest.node_parent.pop(path, None)
    forest.rel_paths.pop(path, None)
    forest.real_paths.pop(path, None)

    if in_ltx:
        forest.files = [item for item in forest.files if item != path]
    if in_xml:
        forest.xml_files = [item for item in forest.xml_files if item != path]

    forest.roots = [item for item in forest.roots if item != path]
    forest.total_files = len(forest.files)
    forest.total_xml_files = len(forest.xml_files)
    _sort_roots(forest)
    _recount_subtrees(forest)
    _invalidate_derived_indexes(forest)
    return True


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