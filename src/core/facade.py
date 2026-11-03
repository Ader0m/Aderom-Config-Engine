# src/core/facade.py
# -*- coding: utf-8 -*-
"""
CoreServices — тонкий фасад над сервисами ядра.

Композиционный корень создаёт один экземпляр и передаёт его:
- платформе UI (для отображения данных);
- модулям через SDK-контекст (ctx.services).

Модули и UI не импортируют внутренние модули ядра напрямую.

Этот фасад НЕ содержит бизнес-логики. Он только делегирует вызовы
специализированным сервисам ядра.
"""

from pathlib import Path

from .external import open_in_notepad_pp
from .fs import find_system_ltx, detect_config_dir
from .tree_query_service import search_nodes, sort_nodes
from .xml_resolver import XmlResolver
from .prebuild_service import PrebuildService


class CoreServices:
    """Тонкий фасад и DI-контейнер для сервисов ядра."""

    def __init__(
        self,
        cache,
        tree_builder,
        tree_query,
        graph_builder,
        resolver_service,
        assembly_service,
        generated_dir,
    ):
        """
        Args:
            cache: FileCacheSystem — общий кэш ядра.
            tree_builder: TreeBuilder — построитель дерева.
            tree_query: TreeQueryService — сервис запросов к дереву.
            graph_builder: ReferenceGraphBuilder — построитель графа связей.
            resolver_service: ResolverService — сервис работы с резолвером.
            assembly_service: AssemblyService — сервис сборки конфигов.
            generated_dir: Директория для сгенерированных файлов.
        """
        # Сохраняем ссылки на реальные сервисы
        self.cache = cache
        self.tree_builder = tree_builder
        self.tree_query = tree_query
        self.graph_builder = graph_builder
        self.resolver = resolver_service
        self.assembly = assembly_service
        self.generated_dir = Path(generated_dir)
        self.prebuild = PrebuildService(resolver_service)

        # Для обратной совместимости (модули пока используют старые имена)
        self._adapter = None  # Устаревшее, использовать через .resolver

    # -------------------------------------------------- кэш

    def invalidate_cache(self):
        """Очистить общий кэш ядра."""
        self.cache.clear()
        if hasattr(self.resolver, "invalidate_cache"):
            self.resolver.invalidate_cache()

    # -------------------------------------------------- фоновая предсборка

    def start_prebuild(self, forest, config_dir):
        """Запустить фоновую сборку всех LTX после построения дерева."""
        self.prebuild.start(forest, config_dir)

    def prebuild_status(self):
        return self.prebuild.status()

    def wait_prebuild(self, log=None):
        self.prebuild.wait(log=log)

    def stop_prebuild(self):
        self.prebuild.stop()

    # -------------------------------------------------- дерево

    def find_system_ltx(self, sel):
        """Найти system.ltx в указанной директории."""
        return find_system_ltx(sel)

    def build_tree(self, scan_dir, system_root, log=None, clean=False):
        """Построить дерево файлов."""
        if clean:
            self.invalidate_cache()
        return self.tree_builder.build(
            scan_dir=scan_dir,
            system_root=system_root,
            log=log,
            use_cache=True,
        )

    def sort_nodes(self, forest, nodes, col="path", reverse=False):
        """Отсортировать узлы дерева."""
        return sort_nodes(forest, nodes, col=col, reverse=reverse)

    def search_nodes(self, forest, text):
        """Поиск по дереву файлов."""
        return search_nodes(forest, text)

    def rel_path(self, forest, path):
        """Получить относительный путь файла."""
        return forest.rel_paths.get(path, str(path))

    # -------------------------------------------------- резолвер (прокси для обратной совместимости)

    def resolver_pass(self, forest, config_dir, log=None):
        """
        Выполнить проход резолвера.

        .. deprecated:: Используйте `self.resolver.run_pass()`.
        """
        return self.resolver.run_pass(forest, config_dir, log=log)

    def assemble_text(self, root_path, forest, config_dir, log=None):
        """
        Собрать текст конфигурации.

        .. deprecated:: Используйте `self.resolver.assemble_text()`.
        """
        return self.resolver.assemble_text(root_path, forest, config_dir, log=log)

    # -------------------------------------------------- сборка (прокси для обратной совместимости)

    def source_map(self, forest):
        """
        Построить карту источников.

        .. deprecated:: Используйте `self.assembly.source_map()`.
        """
        return self.assembly.source_map(forest)

    def assemble_to_file(self, root_path, forest):
        """
        Собрать файл в директорию сгенерированных конфигов.

        .. deprecated:: Используйте `self.assembly.assemble_to_file()`.
        """
        return self.assembly.assemble_to_file(root_path, forest)

    # -------------------------------------------------- граф связей

    def build_section_graph(
        self,
        forest,
        section_name: str,
        vfs_mode: bool = False,
        log=None,
    ):
        """Построить граф связей для секции.

        ``vfs_mode=True`` — полный обход VFS без фильтра LtxForest;
        в результате заполняется ``lost_children``.

        Обычный режим ищет по финальным файлам резолвера. Перед резолвом
        каждого корня проверяется :class:`ResolvedCache`.
        """
        resolved_provider = None

        if not vfs_mode:
            config_dir = Path(getattr(forest, "scan_dir", "") or "")
            reported = {"error": False}

            def resolved_provider(root_path):
                try:
                    text, line_sources = self.resolver.assemble_resolved(
                        root_path,
                        forest,
                        config_dir,
                        log=None,
                    )
                    return text, line_sources
                except Exception as exc:
                    if log and not reported["error"]:
                        log("resolved graph: резолвер недоступен: %s" % exc)
                        reported["error"] = True
                    return None

        try:
            return self.graph_builder.build_for_section(
                forest,
                section_name,
                vfs_mode=vfs_mode,
                resolved_provider=resolved_provider,
                log=log,
            )
        finally:
            if log and hasattr(self.resolver, "cache_stats"):
                stats = self.resolver.cache_stats()
                log(
                    "resolve cache: entries=%d, chars=%d, hits=%d, "
                    "disk_hits=%d, misses=%d"
                    % (
                        stats.get("entries", 0),
                        stats.get("chars", 0),
                        stats.get("hits", 0),
                        stats.get("disk_hits", 0),
                        stats.get("misses", 0),
                    )
                )

    # -------------------------------------------------- внешние инструменты

    def open_notepad(self, path):
        """Открыть файл в Notepad++."""
        return open_in_notepad_pp(path)

    def detect_config_dir(self, candidates):
        """Определить каталог конфигов."""
        return detect_config_dir(candidates)

    def assemble_resolved(self, root_path, forest, config_dir, log=None):
        """Чистый резолв + построчный маппинг владельцев."""
        return self.resolver.assemble_resolved(root_path, forest, config_dir, log=log)

    # -------------------------------------------------- XML-резолв

    def resolve_xml(self, path, config_dir, log=None, dxml=True):
        """Разрешить XML: include-сборка + DXML (XmlResolveOutput)."""
        resolver = XmlResolver(
            config_dir=config_dir,
            log=log,
            dxml=dxml,
        )
        return resolver.resolve_output(path, dxml=dxml)

    def resolve_xml_to_file(self, path, config_dir, log=None, dxml=True):
        """Разрешить XML и сохранить результат в generated/xml/."""
        result = self.resolve_xml(path, config_dir, log=log, dxml=dxml)
        out = self.assembly.write_generated_xml(path, result.text)
        return out
