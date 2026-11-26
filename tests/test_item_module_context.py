# -*- coding: utf-8 -*-
"""Item viewer с реальными SDK-адаптерами и минимальной платформой.

Тест фиксирует границу ``ModuleContext``: запись XML и правка полей должны
идти через ``ctx.documents``, а не через ``ctx.ui``.
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.support import SRC  # noqa: F401

from sdk.contracts import ProjectOverlay
from sdk.features.documents import ResolvedDraftInfo
from sdk.features.items import ItemEntry, LocalizationString
from sdk.kernel import ModuleManifest
from core.localization import edit_localization_text
from app.projects import ProjectService
from ui.bus import EventBus
from ui.platform.sdk_bridge import build_module_context
from modules.item_browser.item_viewer import ItemViewerFeature

RU_XML = """<?xml version="1.0" encoding="windows-1251"?>
<string_table>
\t<string id="st_name"><text>Старое имя</text></string>
</string_table>
"""


class _Platform:
    """Минимальная платформа, которую видят UiApiAdapter/DocumentApiAdapter."""

    def __init__(self, path, project=None):
        self.path = Path(path)
        self.project = project
        self.files = {self.path: RU_XML}
        self.entry = ItemEntry(
            section="item",
            fields=(),
            name_text="Старое имя",
            name_source=LocalizationString(
                "st_name",
                "Старое имя",
                self.path,
            ),
        )
        self.async_text_writes = []
        self.field_edits = []
        self.retains = []
        self.releases = []

    def is_project_active(self):
        return True

    def project_name(self):
        return "Demo"

    def project_overlay(self):
        return ProjectOverlay()

    def document_text(self, path):
        if self.project is not None:
            text = self.project.get_text(Path(path))
            if text is not None:
                return text
        return self.files.get(Path(path), "")

    def write_project_text(self, path, text):
        if self.project is not None:
            self.project.update_draft(path, text)
            return True
        self.files[Path(path)] = str(text)
        return True

    def write_project_text_async(
        self,
        path,
        text,
        on_done=None,
        original_text=None,
    ):
        self.async_text_writes.append(
            (Path(path), str(text), original_text)
        )
        if self.project is not None:
            self.project.update_draft(path, text)
        else:
            self.files[Path(path)] = str(text)
        if on_done is not None:
            on_done(True, None)
        return True

    def resolved_draft(self, tab_id):
        return ResolvedDraftInfo(
            text="[item]\nkind = i_food\n",
            saved_text="",
            original_text="",
            line_sources=(None, None),
        )

    def has_resolved_document(self, tab_id):
        return True

    def resolved_document_title(self, tab_id):
        return "[Res]system.ltx"

    def apply_resolved_field_edit_async(
        self,
        tab_id,
        section,
        key,
        value,
        on_done=None,
        owner=None,
    ):
        self.field_edits.append((tab_id, section, key, value))
        if on_done is not None:
            on_done(True, None)
        return True

    def rename_resolved_field_async(
        self,
        tab_id,
        section,
        old_key,
        new_key,
        value,
        on_done=None,
        owner=None,
    ):
        self.field_edits.append((tab_id, section, new_key, value))
        if on_done is not None:
            on_done(True, None)
        return True

    def retain_resolved_document(self, tab_id):
        self.retains.append(tab_id)
        return True

    def release_resolved_document(self, tab_id):
        self.releases.append(tab_id)
        return True

    def item_entry(self, tab_id, section):
        return self.entry

    def log(self, message):
        pass

    def set_status(self, message, timeout=0):
        pass

    def register_action_handler(
        self,
        action,
        handler,
        priority=0,
        owner=None,
    ):
        return SimpleNamespace(unsubscribe=lambda: None)


class _ItemsProvider:
    def __init__(self, platform):
        self._platform = platform

    def item_entry(self, tab_id, section):
        return self._platform.entry

    def build_item_entry(self, forest, text, section, log=None, overlay=None):
        return self._platform.entry

    def edit_localization_text(self, text, key, value):
        return edit_localization_text(text, key, value)


class _Tasks:
    def run(self, work, on_done=None, on_log=None, **kwargs):
        try:
            result = work(lambda _message: None)
        except Exception as exc:
            if on_done is not None:
                on_done(None, exc)
        else:
            if on_done is not None:
                on_done(result, None)
        return object()

    def operation_active(self, operation_key):
        return False


class _Services:
    def color_definitions(self, forest, overlay=None, config_dir=None):
        return {}


class ItemViewerContextBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.path = (
            Path("gamedata") / "configs" / "text" / "rus" / "ru.xml"
        )
        self.platform = _Platform(self.path)
        self.ctx = build_module_context(
            ModuleManifest(id="item_browser", name="Item browser"),
            sdk_version="0.14.0",
            events=EventBus(),
            commands=SimpleNamespace(),
            ui=self.platform,
            tasks=_Tasks(),
            services=_Services(),
            paths=SimpleNamespace(
                key=lambda path: str(Path(path)).lower(),
                same=lambda left, right: (
                    str(Path(left)).lower() == str(Path(right)).lower()
                ),
            ),
            items=_ItemsProvider(self.platform),
        )
        self.feature = ItemViewerFeature(self.ctx, session=None)
        self.feature._tab_id = "base.resolved:system.ltx"
        self.feature._root_path = Path("system.ltx")
        self.feature._title = "[Res]system.ltx"

    def tearDown(self):
        self.feature.close()

    def test_item_text_uses_document_api(self):
        self.assertTrue(
            self.feature._apply_item_text(
                "base.resolved:system.ltx",
                "item",
                "name",
                "Новое имя",
            )
        )

        self.assertEqual(len(self.platform.async_text_writes), 1)
        path, text, _original = self.platform.async_text_writes[0]
        self.assertEqual(path, self.path)
        self.assertIn("<text>Новое имя</text>", text)

    def test_field_edit_uses_document_api(self):
        self.assertTrue(
            self.feature._apply_field(
                "base.resolved:system.ltx",
                "item",
                "kind",
                "i_med",
            )
        )

        self.assertEqual(
            self.platform.field_edits,
            [("base.resolved:system.ltx", "item", "kind", "i_med")],
        )


class ItemTextToProjectDraftTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.path = (
            root / "gamedata" / "configs" / "text" / "rus" / "ru.xml"
        )
        self.path.parent.mkdir(parents=True)
        self.path.write_text(RU_XML, encoding="utf-8")
        self.project = ProjectService(root / "projects")
        self.project.create("proj")
        self.addCleanup(self.project.close)
        self.platform = _Platform(self.path, project=self.project)
        self.ctx = build_module_context(
            ModuleManifest(id="item_browser", name="Item browser"),
            sdk_version="0.14.0",
            events=EventBus(),
            commands=SimpleNamespace(),
            ui=self.platform,
            tasks=_Tasks(),
            services=_Services(),
            paths=SimpleNamespace(
                key=lambda path: str(Path(path)).lower(),
                same=lambda left, right: (
                    str(Path(left)).lower() == str(Path(right)).lower()
                ),
            ),
            items=_ItemsProvider(self.platform),
        )
        self.feature = ItemViewerFeature(self.ctx, session=None)
        self.feature._tab_id = "base.resolved:system.ltx"
        self.feature._root_path = Path("system.ltx")
        self.feature._title = "[Res]system.ltx"

    def tearDown(self):
        self.feature.close()

    def test_item_text_reaches_project_draft(self):
        self.assertTrue(
            self.feature._apply_item_text(
                "base.resolved:system.ltx",
                "item",
                "name",
                "Новое имя",
            )
        )

        draft = self.project.get_text(self.path)
        self.assertIsNotNone(draft)
        self.assertIn("<text>Новое имя</text>", draft)


if __name__ == "__main__":
    unittest.main()
