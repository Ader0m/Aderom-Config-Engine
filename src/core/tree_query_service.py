# -*- coding: utf-8 -*-
"""TreeQueryService — запросы к уже построенному дереву."""

from .fs import build_source_map, pretty_source


class TreeQueryService:
    """
    Сервис запросов к базовому дереву.

    Он не строит базовое дерево сам.
    Он работает с тем, что построил TreeBuilder.
    """

    def sort_nodes(self, forest, nodes, col="path", reverse=False):
        return sort_nodes(forest, nodes, col=col, reverse=reverse)

    def search_nodes(self, forest, text):
        return search_nodes(forest, text)

    def source_map(self, forest):
        return build_source_map(forest)

    def report(self, forest, col="path", reverse=False):
        return generate_report(forest, col=col, reverse=reverse)


# ------------------------------------------------------------ функции

def node_sort_key(forest, path, col):
    rel = forest.rel_paths.get(path, str(path)).lower()

    if col == "files":
        return (forest.subtree_counts.get(path, 1), rel)

    if col == "children":
        return (len(forest.display_children.get(path, [])), rel)

    return (rel, forest.subtree_counts.get(path, 1))


def sort_nodes(forest, nodes, col="path", reverse=False):
    return sorted(
        nodes,
        key=lambda p: node_sort_key(forest, p, col),
        reverse=reverse,
    )


def search_nodes(forest, text):
    text = (text or "").strip().lower()

    if not text:
        return []

    out = [
        p
        for p in forest.files
        if text in p.name.lower()
        or text in forest.rel_paths.get(p, "").lower()
    ]

    return sort_nodes(forest, out, "path")


def generate_report(forest, col="path", reverse=False) -> str:
    lines = [
        "Scan dir: %s" % forest.scan_dir,
        "System.ltx: %s" % forest.system_root,
        "Resolver sections: %s" % forest.resolver_sections,
        "Total .ltx files: %d" % forest.total_files,
        "Files in system.ltx tree: %d" % forest.system_tree_files,
        "Top-level roots: %d" % len(forest.roots),
        "",
        "root;files_in_tree;direct_children",
    ]

    for p in sort_nodes(forest, forest.roots, col, reverse):
        lines.append(
            "%s;%d;%d"
            % (
                forest.rel_paths.get(p, str(p)),
                forest.subtree_counts.get(p, 1),
                len(forest.display_children.get(p, [])),
            )
        )

    return "\n".join(lines)