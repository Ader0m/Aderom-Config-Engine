# -*- coding: utf-8 -*-
"""Патч-конвейер ядра: планы, генерация, проверка и запись."""

from __future__ import annotations

from dataclasses import replace

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
from core.patching.commit import PatchCommitService
from core.patching.engine import PatchOrchestrator
from core.patching.roundtrip import PatchRoundTripVerifier
from core.patching.writer import PatchWriter


class PatchServices:
    """Патч-конвейер: единственный владелец patch engine."""

    def __init__(self, resolver, cache, xml):
        self.resolver = resolver
        self.xml = xml
        self.patch = PatchPlanner(resolver, cache)
        self.dltx = DltxGenerator(resolver, cache)
        self.dxml = DxmlGenerator(resolver, cache)
        self.patch_verifier = ModelPatchVerifier()
        self.resolver_patch_verifier = ResolverPatchVerifier()
        self.xml_patch_verifier = XmlModelPatchVerifier()
        self.xml_resolver_patch_verifier = XmlResolverPatchVerifier(xml)
        self.xml_fork = XmlForkService(self)
        self.ltx_override = LtxOverrideService()
        self.ltx_in_place = LtxInPlaceService(self)
        self._patch_writer = PatchWriter(
            require_verification=True,
            require_resolver=True,
        )
        self.patch_roundtrip = PatchRoundTripVerifier(resolver)
        self.patch_commit = PatchCommitService(
            self._patch_writer,
            roundtrip=self.patch_roundtrip,
        )
        self.ltx_patch = LtxPatchService(self)
        self.xml_patch = XmlPatchService(self)
        self.patch_orchestrator = PatchOrchestrator(self)

    # -------------------------------------------------- планы
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
        overlay=None,
    ):
        """Построить план изменения сырого XML-файла."""
        return self.patch.plan_xml_document_change(
            source_path,
            baseline_text,
            draft_text,
            forest,
            options=options,
            overlay=overlay,
        )

    def plan_xml_resolved_patch(
        self,
        source_path,
        baseline_text,
        edited_text,
        line_sources=(),
        forest=None,
        options=None,
        overlay=None,
    ):
        """Построить план правки resolved-вида XML."""
        return self.patch.plan_xml_resolved_edit(
            source_path,
            baseline_text,
            edited_text,
            line_sources=line_sources,
            forest=forest,
            options=options,
            overlay=overlay,
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
        """Устаревший In-place: оставлен только для совместимости."""
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
        analysis=None,
        verify_resolver=True,
    ):
        """Единый вход LTX: root / on_file / override."""
        return self.ltx_patch.plan(
            source_path,
            baseline_text,
            edited_text,
            mode=mode,
            line_sources=line_sources,
            forest=forest,
            options=options,
            resolved=resolved,
            analysis=analysis,
            verify_resolver=verify_resolver,
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
        overlay=None,
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
            overlay=overlay,
        )

    # -------------------------------------------------- генерация/проверка
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


__all__ = ["PatchServices"]
