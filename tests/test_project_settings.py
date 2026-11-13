# -*- coding: utf-8 -*-
"""Настройки проекта, которые модули добавляют через SDK."""

import json
import tempfile
import unittest
from pathlib import Path

from PyQt5.QtWidgets import QLabel, QToolButton

from tests.qt_support import qapp
from tests.support import SRC  # noqa: F401

from sdk import ProjectSetting, ProjectSettingChoice
from sdk.adapters import UiApiAdapter
from ui.project import Project, ProjectService
from ui.project_dialogs import (
    ProjectCreateDialog,
    ProjectSettingsDialog,
)
from ui.project_settings import ProjectSettingError, ProjectSettingsRegistry

_app = qapp()


def _choice_setting(key="test.mode", default="override"):
    return ProjectSetting(
        key=key,
        label="Режим теста",
        kind="choice",
        default=default,
        choices=(
            ProjectSettingChoice(value="override", label="Override"),
            ProjectSettingChoice(value="merge", label="Merge"),
        ),
        description="Тестовая настройка",
    )


class ProjectSettingsRegistryTest(unittest.TestCase):
    def test_register_normalize_and_unregister(self):
        registry = ProjectSettingsRegistry()
        registered = registry.register(_choice_setting())

        self.assertEqual(registered.default, "override")
        self.assertEqual(
            [item.key for item in registry.settings()],
            ["test.mode"],
        )
        self.assertEqual(
            registry.normalize("test.mode", "merge"),
            "merge",
        )
        with self.assertRaises(ProjectSettingError):
            registry.normalize("test.mode", "unknown")

        self.assertTrue(registry.unregister("test.mode"))
        self.assertIsNone(registry.get("test.mode"))

    def test_duplicate_is_rejected(self):
        registry = ProjectSettingsRegistry()
        registry.register(_choice_setting())
        with self.assertRaises(ProjectSettingError):
            registry.register(_choice_setting())

    def test_bool_and_text_are_normalized(self):
        registry = ProjectSettingsRegistry()
        registry.register(
            ProjectSetting(
                key="test.flag",
                label="Флаг",
                kind="bool",
                default=False,
            )
        )
        registry.register(
            ProjectSetting(
                key="test.text",
                label="Текст",
                kind="text",
                default="",
            )
        )

        self.assertTrue(registry.normalize("test.flag", 1))
        self.assertEqual(registry.normalize("test.text", 42), "42")


class ProjectSettingPersistenceTest(unittest.TestCase):
    def test_value_is_saved_and_loaded_with_project(self):
        with tempfile.TemporaryDirectory() as d:
            service = ProjectService(Path(d) / "projects")
            project = service.create("settings", save_mode="root")
            service.set_module_setting("test.mode", "merge")

            self.assertEqual(
                service.module_setting("test.mode"),
                "merge",
            )
            self.assertEqual(
                service.module_setting("missing", "fallback"),
                "fallback",
            )

            meta = json.loads(
                (project.root_dir / "project.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                meta["module_settings"],
                {"test.mode": "merge"},
            )

            project_id = project.id
            service.close()
            loaded = service.load(project_id)
            self.assertEqual(
                loaded.module_setting("test.mode"),
                "merge",
            )

    def test_old_project_without_module_settings_loads(self):
        with tempfile.TemporaryDirectory() as d:
            service = ProjectService(Path(d) / "projects")
            project = service.create("old", save_mode="root")
            meta_path = project.root_dir / "project.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta.pop("module_settings", None)
            meta_path.write_text(
                json.dumps(meta, ensure_ascii=False),
                encoding="utf-8",
            )
            project_id = project.id
            service.close()

            loaded = service.load(project_id)
            self.assertEqual(loaded.module_settings, {})


class _Platform:
    def __init__(self):
        self.settings = {}
        self.values = {}

    def register_project_setting(self, setting):
        self.settings[setting.key] = setting
        return True

    def unregister_project_setting(self, key):
        return self.settings.pop(key, None) is not None

    def project_setting_definitions(self):
        return tuple(self.settings.values())

    def project_setting(self, key, default=None):
        if key in self.values:
            return self.values[key]
        setting = self.settings.get(key)
        if setting is not None:
            return setting.default
        return default

    def set_project_setting(self, key, value):
        self.values[key] = value
        return True


class UiApiProjectSettingsTest(unittest.TestCase):
    def test_adapter_registers_and_reads_settings(self):
        platform = _Platform()
        ui = UiApiAdapter(platform)
        setting = _choice_setting()

        self.assertTrue(ui.register_project_setting(setting))
        self.assertEqual(
            [item.key for item in ui.project_settings()],
            ["test.mode"],
        )
        self.assertEqual(
            ui.project_settings()[0].choices[0].value,
            "override",
        )

        self.assertEqual(ui.project_setting("test.mode"), "override")
        self.assertTrue(ui.set_project_setting("test.mode", "merge"))
        self.assertEqual(ui.project_setting("test.mode"), "merge")

        self.assertTrue(ui.unregister_project_setting("test.mode"))
        self.assertEqual(ui.project_settings(), ())


class ProjectSettingsDialogTest(unittest.TestCase):
    def test_dialog_renders_and_returns_module_setting(self):
        project = Project(
            id="p",
            name="P",
            root_dir=Path("."),
        )
        dialog = ProjectSettingsDialog(
            project,
            settings=(_choice_setting(),),
            setting_values={"test.mode": "merge"},
        )
        try:
            self.assertEqual(
                dialog.module_setting_values(),
                {"test.mode": "merge"},
            )
            setting, combo = dialog._setting_widgets["test.mode"]
            self.assertEqual(setting.key, "test.mode")
            self.assertEqual(combo.currentData(), "merge")
            self.assertEqual(combo.count(), 2)
        finally:
            dialog.deleteLater()


class _Sub:
    def unsubscribe(self):
        pass


class _OggUi:
    def __init__(self):
        self.settings = []
        self.logs = []

    def register_project_asset_type(self, _asset_type):
        return True

    def register_project_setting(self, setting):
        self.settings.append(setting)
        return True

    def unregister_project_setting(self, _key):
        return True

    def register_file_reader(self, _reader):
        return _Sub()

    def register_menu(self, *_args, **_kwargs):
        pass

    def register_open_handler(self, _handler):
        pass

    def register_tree_context_action(self, *_args, **_kwargs):
        pass

    def unregister_project_asset_type(self, _key):
        return True

    def log(self, message):
        self.logs.append(message)


class _OggCommands:
    def register(self, *_args, **_kwargs):
        pass


class _OggServices:
    def register_section_graph_contributor(self, _contributor):
        pass


class _OggCtx:
    def __init__(self):
        self.ui = _OggUi()
        self.commands = _OggCommands()
        self.services = _OggServices()

    def log(self, message):
        self.ui.log(message)


class UnifiedProjectDialogTest(unittest.TestCase):
    def test_create_and_settings_share_one_dialog(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "projects"
            service = ProjectService(base)

            create = ProjectCreateDialog(
                settings=(_choice_setting(),),
                setting_values={"test.mode": "override"},
                base_dir=service.base_dir,
                suggest_dir=service.suggest_project_dir,
            )
            try:
                self.assertEqual(create.windowTitle(), "Создать проект")
                self.assertFalse(create.name_edit.isReadOnly())
                self.assertFalse(create.path_edit.isReadOnly())
                self.assertIsNone(create.delete_button)
                self.assertEqual(
                    create.module_setting_values(),
                    {"test.mode": "override"},
                )

                create.name_edit.setText("My Project")
                self.assertIn(
                    "my_project",
                    create.project_directory(),
                )

                # Описания живут в кнопках «?», а не в нижней подсказке.
                help_buttons = create.findChildren(QToolButton)
                help_tips = [button.toolTip() for button in help_buttons]
                self.assertTrue(
                    any("DLTX" in tip for tip in help_tips),
                    help_tips,
                )
                labels = create.findChildren(QLabel)
                self.assertFalse(
                    any(
                        "Root — один DLTX-патч на корень загрузки"
                        in label.text()
                        for label in labels
                    )
                )
            finally:
                create.deleteLater()

            project = service.create("My Project", save_mode="root")
            edit = ProjectSettingsDialog(project)
            try:
                self.assertEqual(edit.windowTitle(), "Настройки проекта")
                self.assertTrue(edit.name_edit.isReadOnly())
                self.assertTrue(edit.path_edit.isReadOnly())
                self.assertIsNotNone(edit.delete_button)
            finally:
                edit.deleteLater()


class OggProjectSettingTest(unittest.TestCase):
    def test_ogg_module_registers_setting_via_sdk(self):
        from modules.ogg_editor.module import (
            OGG_SAVE_SETTING,
            OggEditorModule,
        )

        ctx = _OggCtx()
        module = OggEditorModule()
        module.ctx = ctx
        module._subs = []
        module._tabs = {}
        module._window = None
        module._forest = None
        module._asset_type_registered = False
        module._setting_registered = False
        module._file_reader_sub = None
        module._section_graph_contributor = None
        module._suspend_refresh = False

        module.register(ctx)

        self.assertEqual(
            [setting.key for setting in ctx.ui.settings],
            [OGG_SAVE_SETTING.key],
        )

    def test_ogg_module_declares_override_setting(self):
        from modules.ogg_editor.module import (
            OGG_SAVE_SETTING,
            OggEditorModule,
        )

        self.assertEqual(OGG_SAVE_SETTING.key, "ogg_editor.save_mode")
        self.assertEqual(OGG_SAVE_SETTING.kind, "choice")
        self.assertEqual(OGG_SAVE_SETTING.default, "override")
        self.assertEqual(
            [choice.value for choice in OGG_SAVE_SETTING.choices],
            ["override"],
        )

        registry = ProjectSettingsRegistry()
        registered = registry.register(OGG_SAVE_SETTING)
        self.assertEqual(registered.default, "override")
        self.assertEqual(
            registry.normalize(OGG_SAVE_SETTING.key, "override"),
            "override",
        )

        # Модуль обязан уметь снять свою настройку при выгрузке.
        self.assertTrue(
            hasattr(OggEditorModule, "deactivate")
        )


if __name__ == "__main__":
    unittest.main()
