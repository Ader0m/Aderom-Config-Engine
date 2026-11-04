# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from core.assembly_service import AssemblyService
from core.facade import CoreServices
from core.file_cache import FileCacheSystem
from core.reference_graph import DisabledCache, ReferenceGraphBuilder
from core.resolved_cache import ResolvedCache
from core.resolver_service import ResolverService
from core.tree_builder import TreeBuilder
from core.tree_query_service import TreeQueryService
from core.xml_resolve_service import XmlResolveService
from ui.project import ProjectError, ProjectService


class ProjectServiceTest(unittest.TestCase):
    def test_create_save_close_and_load(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "projects"
            source = Path(d) / "a.ltx"
            source.write_text("[a]\nx = 1\n", encoding="windows-1251")

            service = ProjectService(base)
            project = service.create("Мой проект")

            self.assertTrue(service.is_active)
            self.assertEqual(project.name, "Мой проект")
            self.assertTrue((project.root_dir / "project.json").is_file())

            draft = service.update_draft(source, "[a]\nx = 2\n")
            self.assertTrue(draft.has_changes)
            self.assertTrue(draft.is_dirty)

            service.save_draft(source)
            self.assertFalse(draft.is_dirty)
            self.assertEqual(
                service.get_text(source),
                "[a]\nx = 2\n",
            )

            # Несохранённая правка должна уйти на диск при закрытии.
            service.update_draft(source, "[a]\nx = 3\n")
            self.assertTrue(service.get_draft(source).is_dirty)

            project_id = project.id
            service.close()
            self.assertFalse(service.is_active)

            loaded = service.load(project_id)
            self.assertEqual(loaded.name, "Мой проект")
            self.assertEqual(
                service.get_text(source),
                "[a]\nx = 3\n",
            )

    def test_delete_active_project_removes_directory(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "projects"
            source = Path(d) / "a.ltx"
            source.write_text("[a]\nx = 1\n", encoding="windows-1251")

            service = ProjectService(base)
            project = service.create("Delete me")
            service.update_draft(source, "[a]\nx = 2\n")
            service.save_draft(source)

            root = project.root_dir
            self.assertTrue(root.is_dir())

            self.assertTrue(service.delete_active_project())
            self.assertFalse(service.is_active)
            self.assertFalse(root.exists())
            self.assertEqual(service.list_projects(), [])

    def test_patch_mode_can_be_changed_and_persisted(self):
        with tempfile.TemporaryDirectory() as d:
            service = ProjectService(Path(d) / "projects")
            project = service.create("Mode")

            self.assertEqual(service.patch_mode(), "one_by_one")

            service.set_patch_mode("all_in_one")
            self.assertEqual(service.patch_mode(), "all_in_one")

            project_id = project.id
            service.close()
            service.load(project_id)

            self.assertEqual(service.patch_mode(), "all_in_one")

    def test_autosave_and_flush(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "projects"
            source = Path(d) / "a.ltx"
            source.write_text("[a]\nx = 1\n", encoding="windows-1251")

            service = ProjectService(base)
            service.create("Autosave")
            service.update_draft(source, "[a]\nx = 2\n")

            saved = service.autosave()
            self.assertEqual(len(saved), 1)
            self.assertFalse(service.get_draft(source).is_dirty)

            service.update_draft(source, "[a]\nx = 3\n")
            self.assertTrue(service.get_draft(source).is_dirty)
            service.flush()
            self.assertFalse(service.get_draft(source).is_dirty)

            service.update_resolved_draft(
                "resolved:tab",
                source,
                "RESOLVED: a.ltx",
                "[a]\nx = 3\n",
                "[a]\nx = 4\n",
            )
            saved = service.autosave()
            self.assertTrue(
                any(event.kind == "resolved" for event in saved)
            )
            self.assertFalse(
                service.get_resolved_draft("resolved:tab").is_dirty
            )

    def test_requires_active_project(self):
        with tempfile.TemporaryDirectory() as d:
            service = ProjectService(Path(d) / "projects")
            with self.assertRaises(ProjectError):
                service.update_draft(Path(d) / "a.ltx", "x")

    def test_resolved_draft_persists(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "projects"
            root = Path(d) / "system.ltx"

            service = ProjectService(base)
            project = service.create("Resolved")
            service.update_resolved_draft(
                "resolved:system",
                root,
                "RESOLVED: system.ltx",
                "[a]\nx = 1\n",
                "[a]\nx = 2\n",
            )
            service.save_resolved_draft("resolved:system")

            project_id = project.id
            service.close()
            service.load(project_id)

            self.assertEqual(
                service.get_resolved_text("resolved:system"),
                "[a]\nx = 2\n",
            )
            self.assertEqual(len(service.changed_resolved_drafts()), 1)

    def test_build_preview_uses_real_patch_pipeline(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "gamedata" / "configs"
            cfg.mkdir(parents=True)
            root = cfg / "system.ltx"
            source = cfg / "a.ltx"
            root.write_text('[system]\n#include "a.ltx"\n', encoding="cp1251")
            source.write_text("[w]\ncost = 100\n", encoding="cp1251")

            cache = FileCacheSystem()
            forest = TreeBuilder(cache).build(
                cfg,
                root,
                use_cache=False,
            )
            resolver = ResolverService(cache, ResolvedCache())
            resolver.ensure_adapter(forest, cfg)

            services = CoreServices(
                cache=cache,
                tree_builder=TreeBuilder(cache),
                tree_query=TreeQueryService(),
                graph_builder=ReferenceGraphBuilder(DisabledCache()),
                resolver_service=resolver,
                assembly_service=AssemblyService(
                    resolver,
                    Path(d) / "generated",
                ),
                generated_dir=Path(d) / "generated",
                xml_resolve_service=XmlResolveService(),
            )

            project = ProjectService(Path(d) / "projects")
            project.create("Preview")
            project.update_draft(source, "[w]\ncost = 150\n")

            preview = project.build_preview(services, forest)

            self.assertEqual(len(preview.items), 1)
            self.assertEqual(preview.patch_count, 1)
            self.assertTrue(preview.items[0].ok)
            self.assertIn("cost = 150", preview.items[0].generated.text)

    def test_build_preview_for_multiline_resolved_edit(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "gamedata" / "configs"
            cfg.mkdir(parents=True)
            root = cfg / "system.ltx"
            source = cfg / "a.ltx"
            root.write_text('[system]\n#include "a.ltx"\n', encoding="cp1251")
            source.write_text(
                '[w]\ncost = 100\nname = "Old"\n',
                encoding="cp1251",
            )

            cache = FileCacheSystem()
            forest = TreeBuilder(cache).build(
                cfg,
                root,
                use_cache=False,
            )
            resolver = ResolverService(cache, ResolvedCache())
            resolver.ensure_adapter(forest, cfg)

            services = CoreServices(
                cache=cache,
                tree_builder=TreeBuilder(cache),
                tree_query=TreeQueryService(),
                graph_builder=ReferenceGraphBuilder(DisabledCache()),
                resolver_service=resolver,
                assembly_service=AssemblyService(
                    resolver,
                    Path(d) / "generated",
                ),
                generated_dir=Path(d) / "generated",
                xml_resolve_service=XmlResolveService(),
            )

            baseline, sources = services.assemble_resolved(
                root,
                forest,
                cfg,
            )
            edited = baseline.replace("cost = 100", "cost = 150")
            edited = edited.replace('name = "Old"', 'name = "New"')

            project = ProjectService(Path(d) / "projects")
            project.create("Multiline")
            project.update_resolved_draft(
                "resolved:system",
                root,
                "RESOLVED: system.ltx",
                baseline,
                edited,
                line_sources=sources,
            )

            preview = project.build_preview(services, forest)
            item = preview.items[0]

            self.assertTrue(item.ok, item.error)
            self.assertIn("cost = 150", item.generated.text)
            self.assertIn('name = "New"', item.generated.text)

    def test_all_in_one_merges_files_of_one_root(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "gamedata" / "configs"
            cfg.mkdir(parents=True)
            root = cfg / "system.ltx"
            source_a = cfg / "a.ltx"
            source_b = cfg / "b.ltx"
            root.write_text(
                '[system]\n#include "a.ltx"\n#include "b.ltx"\n',
                encoding="cp1251",
            )
            source_a.write_text("[a]\nx = 1\n", encoding="cp1251")
            source_b.write_text("[b]\ny = 1\n", encoding="cp1251")

            cache = FileCacheSystem()
            forest = TreeBuilder(cache).build(
                cfg,
                root,
                use_cache=False,
            )
            resolver = ResolverService(cache, ResolvedCache())
            resolver.ensure_adapter(forest, cfg)

            services = CoreServices(
                cache=cache,
                tree_builder=TreeBuilder(cache),
                tree_query=TreeQueryService(),
                graph_builder=ReferenceGraphBuilder(DisabledCache()),
                resolver_service=resolver,
                assembly_service=AssemblyService(
                    resolver,
                    Path(d) / "generated",
                ),
                generated_dir=Path(d) / "generated",
                xml_resolve_service=XmlResolveService(),
            )

            project = ProjectService(Path(d) / "projects")
            project.create("All in one", save_mode="all_in_one")
            project.update_draft(source_a, "[a]\nx = 2\n")
            project.update_draft(source_b, "[b]\ny = 2\n")

            preview = project.build_preview(services, forest)

            self.assertEqual(len(preview.items), 1)
            item = preview.items[0]
            self.assertEqual(item.kind, "root")
            self.assertTrue(item.ok, item.error)
            self.assertIn("x = 2", item.generated.text)
            self.assertIn("y = 2", item.generated.text)
            self.assertTrue(
                any("Файлов: 2" in note for note in item.notes)
            )

    def test_build_preview_for_section_rename(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "gamedata" / "configs"
            cfg.mkdir(parents=True)
            root = cfg / "system.ltx"
            source = cfg / "a.ltx"
            root.write_text('[system]\n#include "a.ltx"\n', encoding="cp1251")
            source.write_text("[w]\ncost = 100\n", encoding="cp1251")

            cache = FileCacheSystem()
            forest = TreeBuilder(cache).build(
                cfg,
                root,
                use_cache=False,
            )
            resolver = ResolverService(cache, ResolvedCache())
            resolver.ensure_adapter(forest, cfg)

            services = CoreServices(
                cache=cache,
                tree_builder=TreeBuilder(cache),
                tree_query=TreeQueryService(),
                graph_builder=ReferenceGraphBuilder(DisabledCache()),
                resolver_service=resolver,
                assembly_service=AssemblyService(
                    resolver,
                    Path(d) / "generated",
                ),
                generated_dir=Path(d) / "generated",
                xml_resolve_service=XmlResolveService(),
            )

            baseline, sources = services.assemble_resolved(
                root,
                forest,
                cfg,
            )
            edited = baseline.replace("[w]", "[wr]")

            project = ProjectService(Path(d) / "projects")
            project.create("Rename")
            project.update_resolved_draft(
                "resolved:system",
                root,
                "RESOLVED: system.ltx",
                baseline,
                edited,
                line_sources=sources,
            )

            preview = project.build_preview(services, forest)
            item = preview.items[0]

            self.assertTrue(item.ok, item.error)
            self.assertIn("@[wr]", item.generated.text)
            self.assertIn("!![w]", item.generated.text)

    def test_resolved_edit_overrides_existing_winner(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "gamedata" / "configs"
            cfg.mkdir(parents=True)
            root = cfg / "system.ltx"
            source = cfg / "a.ltx"
            existing_patch = cfg / "mod_system_old.ltx"
            root.write_text('[system]\n#include "a.ltx"\n', encoding="cp1251")
            source.write_text("[w]\ncost = 100\n", encoding="cp1251")
            existing_patch.write_text(
                "![w]\ncost = 200\n",
                encoding="cp1251",
            )

            cache = FileCacheSystem()
            forest = TreeBuilder(cache).build(
                cfg,
                root,
                use_cache=False,
            )
            resolver = ResolverService(cache, ResolvedCache())
            resolver.ensure_adapter(forest, cfg)

            services = CoreServices(
                cache=cache,
                tree_builder=TreeBuilder(cache),
                tree_query=TreeQueryService(),
                graph_builder=ReferenceGraphBuilder(DisabledCache()),
                resolver_service=resolver,
                assembly_service=AssemblyService(
                    resolver,
                    Path(d) / "generated",
                ),
                generated_dir=Path(d) / "generated",
                xml_resolve_service=XmlResolveService(),
            )

            baseline, sources = services.assemble_resolved(
                root,
                forest,
                cfg,
            )
            self.assertIn("cost = 200", baseline)
            edited = baseline.replace("cost = 200", "cost = 300")

            project = ProjectService(Path(d) / "projects")
            project.create("Override Winner")
            project.update_resolved_draft(
                "resolved:system",
                root,
                "RESOLVED: system.ltx",
                baseline,
                edited,
                line_sources=sources,
            )

            preview = project.build_preview(services, forest)
            item = preview.items[0]

            self.assertTrue(item.ok, item.error)
            self.assertIn("cost = 300", item.generated.text)
            self.assertEqual(
                item.generated.files[0].relative_path.name,
                "mod_system_~ace_override_winner.ltx",
            )
            self.assertTrue(item.generated.verification.ok)

    def test_build_preview_for_resolved_draft(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "gamedata" / "configs"
            cfg.mkdir(parents=True)
            root = cfg / "system.ltx"
            source = cfg / "a.ltx"
            root.write_text('[system]\n#include "a.ltx"\n', encoding="cp1251")
            source.write_text("[w]\ncost = 100\n", encoding="cp1251")

            cache = FileCacheSystem()
            forest = TreeBuilder(cache).build(
                cfg,
                root,
                use_cache=False,
            )
            resolver = ResolverService(cache, ResolvedCache())
            resolver.ensure_adapter(forest, cfg)

            services = CoreServices(
                cache=cache,
                tree_builder=TreeBuilder(cache),
                tree_query=TreeQueryService(),
                graph_builder=ReferenceGraphBuilder(DisabledCache()),
                resolver_service=resolver,
                assembly_service=AssemblyService(
                    resolver,
                    Path(d) / "generated",
                ),
                generated_dir=Path(d) / "generated",
                xml_resolve_service=XmlResolveService(),
            )

            baseline, sources = services.assemble_resolved(
                root,
                forest,
                cfg,
            )
            edited = baseline.replace("cost = 100", "cost = 150")
            self.assertNotEqual(baseline, edited)

            project = ProjectService(Path(d) / "projects")
            project.create("Resolved Preview")
            project.update_resolved_draft(
                "resolved:system",
                root,
                "RESOLVED: system.ltx",
                baseline,
                edited,
                line_sources=sources,
            )

            preview = project.build_preview(services, forest)

            self.assertEqual(len(preview.items), 1)
            item = preview.items[0]
            self.assertEqual(item.kind, "resolved")
            self.assertTrue(item.ok, item.error)
            self.assertEqual(item.plan.target.root_path, root)
            self.assertIn("cost = 150", item.generated.text)


if __name__ == "__main__":
    unittest.main()
