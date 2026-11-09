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

from dataclasses import replace
from pathlib import Path

from core.tools.external import open_in_notepad_pp, open_with_shell
from core.tools.fs import find_system_ltx, detect_config_dir, read_text_safe
from core.tree.builder import add_virtual_file, remove_virtual_file
from core.items.catalog import ItemCatalogService
from core.items.icons import ItemIconService
from core.items.localization import LocalizationService
from core.tree.query import search_nodes, sort_nodes
from core.xml.service import XmlResolveService
from core.resolver.prebuild import PrebuildService
from core.patching.models import VerificationResult
from core.patching.planner import PatchPlanner
from core.patching.ltx.generator import DltxGenerator
from core.patching.xml.generator import DxmlGenerator
from core.patching.ltx.verifier import (
    ModelPatchVerifier,
    ResolverPatchVerifier,
)
from core.patching.xml.verifier import (
    XmlModelPatchVerifier,
    XmlResolverPatchVerifier,
)
from core.patching.xml.fork import XmlForkService
from core.patching.ltx.override import LtxOverrideService
from core.patching.ltx.in_place import LtxInPlaceService
from core.patching.ltx.service import LtxPatchService
from core.patching.xml.service import XmlPatchService
from core.patching.writer import PatchWriter


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
        xml_resolve_service=None,
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
            xml_resolve_service: XmlResolveService — кэширующий XML-резолв.
        """
        # Сохраняем ссылки на реальные сервисы
        self.cache = cache
        self.tree_builder = tree_builder
        self.tree_query = tree_query
        self.graph_builder = graph_builder
        self.resolver = resolver_service
        self.assembly = assembly_service
        self.generated_dir = Path(generated_dir)
        self.xml = xml_resolve_service or XmlResolveService()
        self.prebuild = PrebuildService(resolver_service, self.xml)

        # Кэш каталога и иконок переживает перезапуск приложения:
        # ключи включают снимок LTX-поддерева и подписи DDS-атласов.
        item_cache_dir = self.generated_dir / "item_cache"
        self.localization = LocalizationService(cache_dir=item_cache_dir)
        self.items = ItemCatalogService(
            resolver_service,
            cache_dir=item_cache_dir,
            localization_service=self.localization,
        )
        self.item_icons = ItemIconService(cache_dir=item_cache_dir)

        # Патч-конвейер: планирование, генерация, проверка и запись.
        # Запись по умолчанию требует успешной проверки.
        self.patch = PatchPlanner(resolver_service, cache)
        self.dltx = DltxGenerator(resolver_service, cache)
        self.dxml = DxmlGenerator(resolver_service, cache)
        self.patch_verifier = ModelPatchVerifier()
        self.resolver_patch_verifier = ResolverPatchVerifier()
        self.xml_patch_verifier = XmlModelPatchVerifier()
        self.xml_resolver_patch_verifier = XmlResolverPatchVerifier(self.xml)
        self.xml_fork = XmlForkService(self)
        self.ltx_override = LtxOverrideService()
        self.ltx_in_place = LtxInPlaceService(self)
        self.patch_writer = PatchWriter(
            require_verification=True,
            require_resolver=True,
        )

        # Единые формато-специфичные входы (тот же план/результат).
        self.ltx_patch = LtxPatchService(self)
        self.xml_patch = XmlPatchService(self)

        # Для обратной совместимости (модули пока используют старые имена)
        self._adapter = None  # Устаревшее, использовать через .resolver

    # -------------------------------------------------- кэш

    def invalidate_cache(self):
        """Очистить общий кэш ядра."""
        self.cache.clear()
        if hasattr(self.resolver, "invalidate_cache"):
            self.resolver.invalidate_cache()
        if hasattr(self.xml, "clear"):
            self.xml.clear()
        if hasattr(self.items, "clear_memory"):
            self.items.clear_memory()
        if hasattr(self.item_icons, "clear_memory"):
            self.item_icons.clear_memory()
        if hasattr(self.localization, "clear_memory"):
            self.localization.clear_memory()

    # -------------------------------------------------- фоновая предсборка

    def start_prebuild(self, forest, config_dir, overlay=None):
        """Запустить фоновую сборку всех LTX после построения дерева."""
        self.prebuild.start(forest, config_dir, overlay=overlay)

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

    def build_tree(
        self,
        scan_dir,
        system_root,
        log=None,
        clean=False,
        overlay=None,
    ):
        """Построить дерево файлов с наложением черновиков проекта."""
        if clean:
            self.invalidate_cache()
        return self.tree_builder.build(
            scan_dir=scan_dir,
            system_root=system_root,
            log=log,
            use_cache=True,
            overlay=overlay,
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

    def add_virtual_file(self, forest, path, text):
        """Добавить новый черновик в существующее дерево VFS."""
        return add_virtual_file(forest, path, text)

    def remove_virtual_file(self, forest, path):
        """Убрать виртуальный файл из дерева VFS."""
        return remove_virtual_file(forest, path)

    def read_text(self, path):
        """Прочитать текстовый файл с автоопределением кодировки."""
        return read_text_safe(path)

    # -------------------------------------------------- резолвер (прокси для обратной совместимости)

    def resolver_pass(self, forest, config_dir, log=None):
        """
        Выполнить проход резолвера.

        .. deprecated:: Используйте `self.resolver.run_pass()`.
        """
        return self.resolver.run_pass(forest, config_dir, log=log)

    def assemble_text(self, root_path, forest, config_dir, log=None,
                      overlay=None):
        """
        Собрать текст конфигурации.

        .. deprecated:: Используйте `self.resolver.assemble_text()`.
        """
        return self.resolver.assemble_text(
            root_path,
            forest,
            config_dir,
            log=log,
            overlay=overlay,
        )

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

    # -------------------------------------------------- предметы и иконки

    def item_catalog_root(self, forest):
        """Корень, из которого собирается каталог предметов."""
        return self.items.find_root(forest)

    def build_item_catalog(
        self,
        forest,
        log=None,
        resolved_text=None,
        reuse_snapshot_cache=False,
    ):
        """Разобрать корневой файл предметов и вернуть каталог."""
        return self.items.build(
            forest,
            log=log,
            resolved_text=resolved_text,
            reuse_snapshot_cache=reuse_snapshot_cache,
        )

    def read_texture_region(self, path, x, y, width, height):
        """Декодировать прямоугольник DDS в RGBA (с кэшем регионов)."""
        return self.item_icons.read_region(path, x, y, width, height)

    def load_item_icons(self, catalog_key, requests, log=None):
        """Пакетно загрузить иконки: кэш + пул процессов."""
        return self.item_icons.load(
            catalog_key,
            requests,
            log=log,
        )

    # -------------------------------------------------- граф связей

    def build_section_graph(
        self,
        forest,
        section_name: str,
        vfs_mode: bool = False,
        log=None,
        overlay=None,
    ):
        """Построить граф связей для секции.

        ``vfs_mode=True`` — полный обход VFS без фильтра LtxForest;
        в результате заполняется ``lost_children``.

        Обычный режим ищет по финальным файлам резолвера. Перед резолвом
        каждого корня проверяется :class:`ResolvedCache`.
        """
        resolved_provider = None
        xml_provider = None

        config_dir = Path(getattr(forest, "scan_dir", "") or "")
        reported = {"error": False}

        def resolved_provider(root_path):
            try:
                text, line_sources = self.resolver.assemble_resolved(
                    root_path,
                    forest,
                    config_dir,
                    log=None,
                    overlay=overlay,
                )
                return text, line_sources
            except Exception as exc:
                if log and not reported["error"]:
                    log("resolved graph: резолвер недоступен: %s" % exc)
                    reported["error"] = True
                return None

        def xml_provider(xml_path):
            try:
                result = self.resolve_xml(
                    xml_path,
                    config_dir,
                    log=None,
                )
                return result.text, result.line_sources
            except Exception as exc:
                if log and not reported["error"]:
                    log("resolved xml: XML-резолвер недоступен: %s" % exc)
                    reported["error"] = True
                return None

        try:
            return self.graph_builder.build_for_section(
                forest,
                section_name,
                vfs_mode=vfs_mode,
                resolved_provider=resolved_provider,
                xml_provider=xml_provider,
                log=log,
            )
        finally:
            if log:
                if hasattr(self.resolver, "cache_stats"):
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
                if hasattr(self.xml, "cache_stats"):
                    stats = self.xml.cache_stats()
                    log(
                        "xml cache: entries=%d, chars=%d, hits=%d, "
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

    def open_with_shell(self, path):
        """Открыть файл приложением Windows по умолчанию.

        Если ассоциация не задана, оболочка показывает диалог «Открыть
        с помощью…». Возвращает ``(ok, message)``.
        """
        return open_with_shell(path)

    def detect_config_dir(self, candidates):
        """Определить каталог конфигов."""
        return detect_config_dir(candidates)

    def assemble_resolved(self, root_path, forest, config_dir, log=None,
                          overlay=None):
        """Чистый резолв + построчный маппинг владельцев."""
        return self.resolver.assemble_resolved(
            root_path,
            forest,
            config_dir,
            log=log,
            overlay=overlay,
        )

    # -------------------------------------------------- XML-резолв

    def resolve_xml(self, path, config_dir, log=None, dxml=True):
        """Разрешить XML: include-сборка + DXML (XmlResolveOutput)."""
        return self.xml.resolve_output(
            path,
            config_dir,
            log=log,
            dxml=dxml,
        )

    def resolve_xml_to_file(self, path, config_dir, log=None, dxml=True):
        """Разрешить XML и сохранить результат в generated/xml/."""
        result = self.resolve_xml(path, config_dir, log=log, dxml=dxml)
        out = self.assembly.write_generated_xml(path, result.text)
        return out

    # -------------------------------------------------- патч-конвейер

    def plan_document_patch(
        self,
        source_path,
        baseline_text,
        draft_text,
        forest,
        options=None,
    ):
        """Построить план изменения документа (без записи)."""
        return self.patch.plan_document_change(
            source_path,
            baseline_text,
            draft_text,
            forest,
            options=options,
        )

    def plan_xml_document_patch(
        self,
        source_path,
        baseline_text,
        draft_text,
        forest,
        options=None,
    ):
        """Построить план изменения сырого XML-файла."""
        return self.patch.plan_xml_document_change(
            source_path,
            baseline_text,
            draft_text,
            forest,
            options=options,
        )

    def plan_xml_resolved_patch(
        self,
        source_path,
        baseline_text,
        edited_text,
        line_sources=(),
        forest=None,
        options=None,
    ):
        """Построить план правки resolved-вида XML."""
        return self.patch.plan_xml_resolved_edit(
            source_path,
            baseline_text,
            edited_text,
            line_sources=line_sources,
            forest=forest,
            options=options,
        )

    def plan_xml_fork(
        self,
        source_path,
        baseline_text,
        edited_text,
        forest=None,
        options=None,
        project_mode="root",
    ):
        """Форк XML-ключа и перепривязка ссылок."""
        return self.xml_fork.plan(
            source_path,
            baseline_text,
            edited_text,
            forest=forest,
            options=options,
            project_mode=project_mode,
        )

    def plan_ltx_override(
        self,
        source_path,
        baseline_text,
        edited_text,
        line_sources=(),
        forest=None,
        options=None,
        resolved=False,
    ):
        """DLTX Override: полная копия LTX-файла-победителя."""
        return self.ltx_override.plan(
            source_path,
            baseline_text,
            edited_text,
            line_sources=line_sources,
            forest=forest,
            options=options,
            resolved=resolved,
        )

    def plan_ltx_in_place(
        self,
        source_path,
        baseline_text,
        edited_text,
        line_sources=(),
        forest=None,
        options=None,
        resolved=False,
    ):
        """DLTX In-place: патч для файла-победителя каждой строки."""
        return self.ltx_in_place.plan(
            source_path,
            baseline_text,
            edited_text,
            line_sources=line_sources,
            forest=forest,
            options=options,
            resolved=resolved,
        )

    def plan_ltx_patch(
        self,
        source_path,
        baseline_text,
        edited_text,
        mode="root",
        line_sources=(),
        forest=None,
        options=None,
        resolved=False,
    ):
        """Единый вход LTX: root / in_place / override."""
        return self.ltx_patch.plan(
            source_path,
            baseline_text,
            edited_text,
            mode=mode,
            line_sources=line_sources,
            forest=forest,
            options=options,
            resolved=resolved,
        )

    def plan_xml_patch(
        self,
        source_path,
        baseline_text,
        edited_text,
        mode="auto",
        line_sources=(),
        forest=None,
        options=None,
        project_mode=None,
    ):
        """Единый вход XML: auto/fork, full_copy, dxml_lua."""
        return self.xml_patch.plan(
            source_path,
            baseline_text,
            edited_text,
            mode=mode,
            line_sources=line_sources,
            forest=forest,
            options=options,
            project_mode=project_mode,
        )

    def generate_patch(self, plan):
        """Сгенерировать патч и проверить доступными способами."""
        if plan.is_xml:
            generated = self.dxml.generate(plan)
            model = self.xml_patch_verifier.verify(generated)
            resolver = self.xml_resolver_patch_verifier.verify(generated)
            combined = VerificationResult(
                ok=model.ok and resolver.ok,
                checks=tuple(model.checks) + tuple(resolver.checks),
                issues=tuple(model.issues) + tuple(resolver.issues),
            )
            return replace(generated, verification=combined)

        generated = self.dltx.generate(plan)
        resolver = self.verify_patch_with_resolver(generated)

        # У объединённого плана нет одного документа-донора diff,
        # поэтому модельная проверка неприменима. Его проверяет Resolver4.
        if plan.diff is None:
            return replace(generated, verification=resolver)

        model = self.patch_verifier.verify(generated)

        combined = VerificationResult(
            ok=model.ok and resolver.ok,
            checks=tuple(model.checks) + tuple(resolver.checks),
            issues=tuple(model.issues) + tuple(resolver.issues),
        )

        return replace(generated, verification=combined)

    def verify_patch(self, generated):
        """Проверить уже сгенерированный патч моделью документа."""
        if generated.plan.is_xml:
            return self.xml_patch_verifier.verify(generated)
        return self.patch_verifier.verify(generated)

    def verify_patch_with_resolver(self, generated):
        """Проверить патч настоящим Resolver4 без записи в VFS."""
        if generated.plan.is_xml:
            return self.xml_resolver_patch_verifier.verify(generated)

        adapter = None
        getter = getattr(self.resolver, "get_adapter", None)
        if callable(getter):
            adapter = getter()

        verifier = ResolverPatchVerifier(adapter)
        return verifier.verify(generated)

    def write_patch(
        self,
        generated,
        patch_mod_root,
        forbidden_roots=(),
        overwrite=False,
    ):
        """Записать проверенный патч в отдельный мод."""
        return self.patch_writer.write(
            generated,
            patch_mod_root,
            forbidden_roots=forbidden_roots,
            overwrite=overwrite,
        )
