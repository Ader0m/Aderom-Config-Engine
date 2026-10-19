# -*- coding: utf-8 -*-
"""ReferenceGraphBuilder — построение графа связей."""


class ReferenceGraphBuilder:
    """
    Построитель графа связей.

    Сейчас реализована только та часть, которая фактически уже была
    в старом дереве: файловые зависимости и родительские связи.

    Секционный граф связей, ссылки по значениям, наследование секций
    и использование идентификаторов являются новой функциональностью,
    поэтому пока не реализуются.
    """

    def __init__(self, cache=None):
        self.cache = cache

    def build_file_dependency_graph(self, forest):
        """
        Возвращает файловый граф зависимостей на основе уже построенного
        базового дерева.
        """
        children = {}

        for p in forest.files:
            children[p] = list(forest.display_children.get(p, []))

        return {
            "roots": list(forest.roots),
            "files": list(forest.files),
            "children": children,
            "parent": dict(forest.node_parent),
            "subtree_counts": dict(forest.subtree_counts),
            "include_children": {
                k: set(v)
                for k, v in getattr(forest, "include_children", {}).items()
            },
            "include_parents": {
                k: set(v)
                for k, v in getattr(forest, "include_parents", {}).items()
            },
            "dltx_attachments": {
                k: set(v)
                for k, v in getattr(forest, "dltx_attachments", {}).items()
            },
        }

    def build_section_reference_graph(self, forest):
        raise NotImplementedError(
            "ReferenceGraphBuilder.build_section_reference_graph ещё не реализован. "
            "В старом core.py этой логики не было."
        )