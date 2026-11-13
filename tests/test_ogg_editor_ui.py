# -*- coding: utf-8 -*-
"""Smoke-тесты вкладки редактора .ogg."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt5.QtCore import QMimeData, Qt
from PyQt5.QtWidgets import QDoubleSpinBox, QStyleOptionViewItem

from tests.qt_support import qapp
from tests.support import SRC  # noqa: F401

from sdk import ITEM_SECTION_MIME, encode_item_section
from tests.test_ogg_comment import _make_file

from modules.ogg_editor import editor_tab as editor_tab_module
from modules.ogg_editor import module as ogg_module
from modules.ogg_editor.editor_tab import OggEditorTab
from modules.ogg_editor.explorer import (
    FIELD_COLUMNS,
    NAME_COLUMN,
    SELECT_COLUMN,
    STATE_COLUMN,
    OggExplorerWindow,
    SoundEntry,
    build_sound_tree,
    filter_sound_tree,
)
from modules.ogg_editor.module import OggEditorModule
from modules.ogg_editor.multi_edit import (
    MultiEditDialog,
    MultiEditValues,
    common_values,
)
from modules.ogg_editor.reader import OggCommentReader
from modules.ogg_editor.ogg import (
    XraySoundComment,
    build_comment_packet,
    build_xray_comment,
    read_comments,
    parse_xray_comment,
)

_app = qapp()


class _Sub:
    def unsubscribe(self):
        pass


class _Events:
    def subscribe(self, *_args, **_kwargs):
        return _Sub()

    def publish(self, *_args, **_kwargs):
        pass


class _Ui:
    def __init__(self):
        self.saved = {}
        self.dirty = []

    def is_project_active(self):
        return True

    def theme_color(self, name, fallback=None):
        return fallback

    def read_project_asset(self, path):
        return None

    def update_project_asset(self, path, data):
        self.saved[str(path)] = data
        return True

    def save_project_asset(self, path, data=None):
        self.saved[str(path)] = data
        return True

    def project_asset_drafts(self):
        return ()

    def set_editor_tab_dirty(self, tab_id, dirty):
        self.dirty.append((tab_id, dirty))

    def save_project_now(self):
        return len(self.saved)

    def set_status(self, message, timeout=0):
        self.status = message

    def copy_to_clipboard(self, text):
        self.clipboard = text

    def show_error(self, text):
        self.error = text

    def log(self, message):
        pass


class _Tasks:
    def __init__(self):
        self.calls = 0

    def run(self, fn, on_done=None, on_log=None):
        self.calls += 1
        result = fn(lambda message: None)
        if on_done is not None:
            on_done(result, None)


class _Ctx:
    def __init__(self):
        self.ui = _Ui()
        self.events = _Events()
        self.tasks = _Tasks()

    def log(self, message):
        self.ui.log(message)


class OggEditorUiTest(unittest.TestCase):
    def test_tab_shows_and_saves_xray_fields(self):
        # В offscreen-тестах мультимедиа-бэкенд Qt может быть недоступен.
        editor_tab_module.QMediaPlayer = None
        editor_tab_module.QMediaContent = None
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "a.ogg"
            comments = build_comment_packet(
                "enc",
                (
                    build_xray_comment(
                        XraySoundComment(min_dist=2.0, max_dist=30.0)
                    ),
                ),
            )
            path.write_bytes(_make_file(comments))

            ctx = _Ctx()
            tab = OggEditorTab(ctx, "ogg_editor.test", path)
            try:
                text = tab.editor.toPlainText()
                self.assertIn("min_dist = 2", text)
                self.assertIn("max_dist = 30", text)

                index = tab.sound_type_combo.basic.findData(0x80200000)
                tab.sound_type_combo.basic.setCurrentIndex(index)
                self.assertIn(
                    "game_type = 2149580800",
                    tab.editor.toPlainText(),
                )

                tab.editor.setPlainText(
                    tab.editor.toPlainText().replace(
                        "max_dist = 30",
                        "max_dist = 77",
                    )
                )
                self.assertIn(str(path), ctx.ui.saved)

                tab.save()
                data = ctx.ui.saved[str(path)]
                parsed = parse_xray_comment(
                    read_comments(data).engine_comment
                )
                self.assertAlmostEqual(parsed.max_dist, 77.0)
                self.assertEqual(parsed.game_type, 0x80200000)

            finally:
                tab.dispose()

    def test_module_manifest_and_extension_filter(self):
        module = OggEditorModule()
        self.assertEqual(module.manifest.name, "ogg_editor")
        self.assertEqual(module.manifest.sdk_min, "0.1.0")
        self.assertEqual(module.manifest.requires, ("base",))

    def test_common_values_show_dash_only_for_mixed(self):
        first = XraySoundComment(
            min_dist=1.0,
            max_dist=30.0,
            base_volume=0.5,
            game_type=0x80200000,
        )
        second = XraySoundComment(
            min_dist=1.0,
            max_dist=40.0,
            base_volume=0.5,
            game_type=0x80200000,
        )

        common = common_values([first, second])
        self.assertAlmostEqual(common.min_dist, 1.0)
        self.assertIsNone(common.max_dist)
        self.assertAlmostEqual(common.base_volume, 0.5)
        self.assertEqual(common.game_type, 0x80200000)

        same = common_values([first, first])
        self.assertAlmostEqual(same.max_dist, 30.0)

        dialog = MultiEditDialog(count=2, initial=common)
        try:
            self.assertEqual(dialog.max_spin.text(), "-")
            self.assertAlmostEqual(dialog.min_spin.value(), 1.0)
            self.assertAlmostEqual(dialog.volume_spin.value(), 0.5)
            self.assertEqual(
                dialog.type_combo.value(),
                0x80200000,
            )
            self.assertTrue(
                dialog.testAttribute(Qt.WA_StyledBackground)
            )
            self.assertIn(
                "QDialog#multiEditDialog",
                dialog.styleSheet(),
            )
            self.assertIn(
                "background-color: #1E1F22",
                dialog.styleSheet(),
            )
        finally:
            dialog.close()

    def test_multi_edit_dialog_uses_supplied_theme(self):
        def theme_color(name, fallback=None):
            return {
                "bg_main": "#101010",
                "text": "#EEEEEE",
                "border": "#444444",
            }.get(name, fallback)

        dialog = MultiEditDialog(
            count=1,
            theme_color=theme_color,
        )
        try:
            sheet = dialog.styleSheet()
            self.assertIn("#101010", sheet)
            self.assertIn("#EEEEEE", sheet)
            self.assertIn("#444444", sheet)
        finally:
            dialog.close()

    def test_multi_edit_merges_only_defined_fields(self):
        base = XraySoundComment(
            version=1,
            min_dist=2.0,
            max_dist=30.0,
            game_type=0x80200000,
        )

        merged = OggEditorModule._merge_multi_values(
            base,
            MultiEditValues(min_dist=5.0),
        )
        self.assertEqual(merged.version, 1)
        self.assertAlmostEqual(merged.min_dist, 5.0)
        self.assertAlmostEqual(merged.max_dist, 30.0)
        self.assertEqual(merged.game_type, 0x80200000)

        merged = OggEditorModule._merge_multi_values(
            base,
            MultiEditValues(base_volume=0.5),
        )
        self.assertEqual(merged.version, 2)
        self.assertAlmostEqual(merged.base_volume, 0.5)

        merged = OggEditorModule._merge_multi_values(
            base,
            MultiEditValues(max_ai_dist=70.0),
        )
        self.assertEqual(merged.version, 3)
        self.assertAlmostEqual(merged.max_ai_dist, 70.0)

    def test_edit_file_params_saves_single_cell_change(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "a.ogg"
            comments = build_comment_packet(
                "enc",
                (build_xray_comment(XraySoundComment(max_dist=30.0)),),
            )
            path.write_bytes(_make_file(comments))

            ctx = _Ctx()
            module = OggEditorModule()
            module.ctx = ctx
            module._tabs = {}
            module._suspend_refresh = False

            ok, error = module.edit_file_params(
                path,
                XraySoundComment(
                    version=3,
                    min_dist=3.0,
                    max_dist=55.0,
                    base_volume=0.7,
                    game_type=0x80200000,
                    max_ai_dist=45.0,
                ),
            )
            self.assertTrue(ok, error)
            data = ctx.ui.saved[str(path)]
            parsed = parse_xray_comment(read_comments(data).engine_comment)
            self.assertAlmostEqual(parsed.min_dist, 3.0)
            self.assertAlmostEqual(parsed.max_dist, 55.0)
            self.assertEqual(parsed.game_type, 0x80200000)

    def test_multi_edit_applies_one_parameter_set(self):
        with tempfile.TemporaryDirectory() as d:
            comments = build_comment_packet(
                "enc",
                (build_xray_comment(XraySoundComment()),),
            )
            paths = []
            for name in ("a.ogg", "b.ogg"):
                path = Path(d) / name
                path.write_bytes(_make_file(comments))
                paths.append(path)

            class Dialog:
                def __init__(self, *args, **kwargs):
                    pass

                def exec_(self):
                    return True

                def values(self):
                    return MultiEditValues(
                        version=3,
                        min_dist=9.0,
                        max_dist=99.0,
                        base_volume=0.4,
                        game_type=0x80200000,
                        max_ai_dist=77.0,
                    )

            ctx = _Ctx()
            module = OggEditorModule()
            module.ctx = ctx
            module._window = None
            module._suspend_refresh = False

            previous = ogg_module.MultiEditDialog
            ogg_module.MultiEditDialog = Dialog
            try:
                module.multi_edit(paths)
            finally:
                ogg_module.MultiEditDialog = previous

            self.assertEqual(len(ctx.ui.saved), 2)
            for path in paths:
                parsed = parse_xray_comment(
                    read_comments(ctx.ui.saved[str(path)]).engine_comment
                )
                self.assertAlmostEqual(parsed.min_dist, 9.0)
                self.assertAlmostEqual(parsed.max_dist, 99.0)
                self.assertEqual(parsed.game_type, 0x80200000)

    def test_corrupted_crc_disables_save(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "bad.ogg"
            comments = build_comment_packet(
                "enc",
                (build_xray_comment(XraySoundComment()),),
            )
            data = bytearray(_make_file(comments))
            data[-1] ^= 0xFF
            path.write_bytes(bytes(data))

            ctx = _Ctx()
            tab = OggEditorTab(ctx, "ogg_editor.bad", path)
            try:
                self.assertFalse(tab._crc_ok)
                self.assertTrue(tab.editor.isReadOnly())
                self.assertFalse(tab.save_button.isEnabled())
                self.assertIn("CRC", tab.status_label.text())
            finally:
                tab.dispose()

    def test_explorer_loads_comment_fields(self):
        with tempfile.TemporaryDirectory() as d:
            game = Path(d) / "gamedata"
            sounds = game / "sounds"
            sounds.mkdir(parents=True)
            comments = build_comment_packet(
                "enc",
                (
                    build_xray_comment(
                        XraySoundComment(
                            version=3,
                            min_dist=2.0,
                            max_dist=42.0,
                            base_volume=0.5,
                            game_type=0x80200000,
                            max_ai_dist=35.0,
                        )
                    ),
                ),
            )
            (sounds / "a.ogg").write_bytes(_make_file(comments))

            ctx = _Ctx()
            window = OggExplorerWindow(
                ctx,
                on_open=lambda path: None,
                on_add=lambda: None,
            )
            try:
                window.set_game_root(game)
                window._load_visible_fields()

                item = next(
                    item
                    for item in window._iter_items()
                    if item.text(NAME_COLUMN) == "a.ogg"
                )
                self.assertEqual(item.text(FIELD_COLUMNS[0]), "3")
                self.assertEqual(item.text(FIELD_COLUMNS[1]), "2")
                self.assertEqual(item.text(FIELD_COLUMNS[2]), "42")
                self.assertEqual(item.text(FIELD_COLUMNS[3]), "0.5")
                self.assertIn(
                    "Оружие",
                    item.text(FIELD_COLUMNS[4]),
                )
                self.assertEqual(item.text(FIELD_COLUMNS[5]), "35")
            finally:
                window.close()

    def test_cell_edit_updates_item_without_opening_file(self):
        with tempfile.TemporaryDirectory() as d:
            game = Path(d) / "gamedata"
            sounds = game / "sounds"
            sounds.mkdir(parents=True)
            comments = build_comment_packet(
                "enc",
                (
                    build_xray_comment(
                        XraySoundComment(min_dist=2.0, max_dist=42.0)
                    ),
                ),
            )
            path = sounds / "a.ogg"
            path.write_bytes(_make_file(comments))

            edited = []

            def on_edit_field(target, params):
                edited.append((Path(target), params))
                return True, ""

            ctx = _Ctx()
            window = OggExplorerWindow(
                ctx,
                on_open=lambda target: None,
                on_add=lambda: None,
                on_edit_field=on_edit_field,
            )
            try:
                window.set_game_root(game)
                window.pin_paths([path])
                window._load_visible_fields()
                item = next(
                    item
                    for item in window._iter_items()
                    if item.text(NAME_COLUMN) == "a.ogg"
                )
                pinned_item = window.pinned_list.topLevelItem(0)

                window.apply_cell_edit(item, FIELD_COLUMNS[1], 7.0)
                self.assertEqual(len(edited), 1)
                self.assertEqual(edited[0][0], path)
                self.assertAlmostEqual(edited[0][1].min_dist, 7.0)
                self.assertEqual(item.text(FIELD_COLUMNS[1]), "7")
                self.assertEqual(
                    pinned_item.text(FIELD_COLUMNS[1]),
                    "7",
                )
            finally:
                window.close()

    def test_cell_delegate_creates_numeric_editor(self):
        with tempfile.TemporaryDirectory() as d:
            game = Path(d) / "gamedata"
            sounds = game / "sounds"
            sounds.mkdir(parents=True)
            comments = build_comment_packet(
                "enc",
                (build_xray_comment(XraySoundComment(min_dist=2.0)),),
            )
            (sounds / "a.ogg").write_bytes(_make_file(comments))

            edited = []

            def on_edit_field(target, params):
                edited.append(params)
                return True, ""

            ctx = _Ctx()
            window = OggExplorerWindow(
                ctx,
                on_open=lambda target: None,
                on_add=lambda: None,
                on_edit_field=on_edit_field,
            )
            try:
                window.set_game_root(game)
                window._load_visible_fields()
                item = next(
                    item
                    for item in window._iter_items()
                    if item.text(NAME_COLUMN) == "a.ogg"
                )
                index = window.tree.indexFromItem(
                    item,
                    FIELD_COLUMNS[1],
                )
                delegate = window.tree.itemDelegate()
                editor = delegate.createEditor(
                    window.tree.viewport(),
                    QStyleOptionViewItem(),
                    index,
                )
                self.assertIsInstance(editor, QDoubleSpinBox)

                delegate.setEditorData(editor, index)
                editor.setValue(8.5)
                delegate.setModelData(
                    editor,
                    window.tree.model(),
                    index,
                )

                self.assertEqual(len(edited), 1)
                self.assertAlmostEqual(edited[0].min_dist, 8.5)
                self.assertEqual(item.text(FIELD_COLUMNS[1]), "8.5")
            finally:
                window.close()

    def test_pinned_files_and_item_drop(self):
        with tempfile.TemporaryDirectory() as d:
            game = Path(d) / "gamedata"
            weapon = game / "sounds" / "weapon"
            weapon.mkdir(parents=True)
            sound = weapon / "shot.ogg"
            sound.write_bytes(b"x")

            ctx = _Ctx()
            window = OggExplorerWindow(
                ctx,
                on_open=lambda target: None,
                on_add=lambda: None,
            )
            try:
                window.set_game_root(game)
                window.pin_paths([sound])
                self.assertTrue(window.is_pinned(sound))
                self.assertEqual(window.pinned_paths(), [sound])
                self.assertTrue(window.unpin_all_button.isEnabled())

                window._load_visible_fields()
                pinned_item = window.pinned_list.topLevelItem(0)
                self.assertEqual(pinned_item.text(STATE_COLUMN), "")
                self.assertTrue(pinned_item.text(FIELD_COLUMNS[0]))

                window._unpin_all()
                self.assertEqual(window.pinned_paths(), [])
                self.assertFalse(window.unpin_all_button.isEnabled())

                window.pin_paths([sound])
                window.unpin_path(sound)
                self.assertFalse(window.is_pinned(sound))

                mime = QMimeData()
                mime.setData(
                    ITEM_SECTION_MIME,
                    encode_item_section(
                        "snd_test",
                        (("snd_shoot", "weapon\\shot"),),
                    ),
                )
                self.assertTrue(window.handle_item_drop(mime))
                self.assertTrue(window.is_pinned(sound))
            finally:
                window.close()

    def test_pinned_fields_refresh_after_save(self):
        with tempfile.TemporaryDirectory() as d:
            game = Path(d) / "gamedata"
            sounds = game / "sounds"
            sounds.mkdir(parents=True)
            comments = build_comment_packet(
                "enc",
                (build_xray_comment(XraySoundComment(max_dist=30.0)),),
            )
            path = sounds / "a.ogg"
            path.write_bytes(_make_file(comments))

            ctx = _Ctx()
            window = OggExplorerWindow(
                ctx,
                on_open=lambda target: None,
                on_add=lambda: None,
            )
            try:
                window.set_game_root(game)
                window.pin_paths([path])
                window._load_visible_fields()
                pinned_item = window.pinned_list.topLevelItem(0)
                self.assertEqual(pinned_item.text(FIELD_COLUMNS[2]), "30")

                changed = _make_file(
                    build_comment_packet(
                        "enc",
                        (
                            build_xray_comment(
                                XraySoundComment(max_dist=77.0)
                            ),
                        ),
                    )
                )
                path.write_bytes(changed)
                window.reload()
                window._load_visible_fields()

                pinned_item = window.pinned_list.topLevelItem(0)
                self.assertEqual(pinned_item.text(FIELD_COLUMNS[2]), "77")
            finally:
                window.close()

    def test_pinned_selection_is_synchronized_with_tree(self):
        with tempfile.TemporaryDirectory() as d:
            game = Path(d) / "gamedata"
            sounds = game / "sounds"
            sounds.mkdir(parents=True)
            comments = build_comment_packet(
                "enc",
                (build_xray_comment(XraySoundComment()),),
            )
            path = sounds / "a.ogg"
            path.write_bytes(_make_file(comments))

            ctx = _Ctx()
            window = OggExplorerWindow(
                ctx,
                on_open=lambda target: None,
                on_add=lambda: None,
            )
            try:
                window.set_game_root(game)
                window.pin_paths([path])
                self.assertTrue(
                    window.tree.isColumnHidden(SELECT_COLUMN)
                )
                self.assertTrue(
                    window.pinned_list.isColumnHidden(SELECT_COLUMN)
                )
                window.enter_selection_mode()
                self.assertFalse(
                    window.tree.isColumnHidden(SELECT_COLUMN)
                )
                self.assertFalse(
                    window.pinned_list.isColumnHidden(SELECT_COLUMN)
                )

                main_item = next(
                    item
                    for item in window._iter_items()
                    if item.text(NAME_COLUMN) == "a.ogg"
                )
                pinned_item = window.pinned_list.topLevelItem(0)

                main_item.setCheckState(SELECT_COLUMN, Qt.Checked)
                self.assertEqual(
                    pinned_item.checkState(SELECT_COLUMN),
                    Qt.Checked,
                )
                self.assertEqual(window.selected_files(), [path])
                self.assertIn(
                    "#CCA700",
                    window.edit_selected_button.styleSheet(),
                )

                window._on_item_clicked(pinned_item, NAME_COLUMN)
                self.assertEqual(
                    main_item.checkState(SELECT_COLUMN),
                    Qt.Unchecked,
                )
                self.assertEqual(window.selected_files(), [])
                self.assertEqual(
                    window.edit_selected_button.styleSheet(),
                    "",
                )
            finally:
                window.close()

    def test_missing_project_shows_popup(self):
        ctx = _Ctx()
        ctx.ui.is_project_active = lambda: False
        window = OggExplorerWindow(
            ctx,
            on_open=lambda path: None,
            on_add=lambda: None,
        )
        try:
            with patch(
                "modules.ogg_editor.explorer.QMessageBox.warning"
            ) as warning:
                self.assertFalse(window._require_project())
            warning.assert_called_once()
            self.assertEqual(
                warning.call_args[0][1],
                "Создайте проект",
            )
        finally:
            window.close()

    def test_copy_sound_reference_path(self):
        with tempfile.TemporaryDirectory() as d:
            game = Path(d) / "gamedata"
            sound = game / "sounds" / "weapons" / "kar98k"
            sound.mkdir(parents=True)
            path = sound / "kar98k_shoot.ogg"
            path.write_bytes(b"x")

            ctx = _Ctx()
            window = OggExplorerWindow(
                ctx,
                on_open=lambda target: None,
                on_add=lambda: None,
            )
            try:
                window.set_game_root(game)
                window._copy_sound_path(path)
                self.assertEqual(
                    ctx.ui.clipboard,
                    "weapons\\kar98k\\kar98k_shoot",
                )
            finally:
                window.close()

    def test_selection_mode_locks_controls_and_highlights_rows(self):
        with tempfile.TemporaryDirectory() as d:
            game = Path(d) / "gamedata"
            sounds = game / "sounds"
            sounds.mkdir(parents=True)
            comments = build_comment_packet(
                "enc",
                (build_xray_comment(XraySoundComment()),),
            )
            path = sounds / "a.ogg"
            path.write_bytes(_make_file(comments))

            ctx = _Ctx()
            window = OggExplorerWindow(
                ctx,
                on_open=lambda target: None,
                on_add=lambda: None,
            )
            try:
                window.set_game_root(game)
                window.pin_paths([path])
                window.enter_selection_mode()

                self.assertFalse(window.refresh_button.isEnabled())
                self.assertFalse(window.section_button.isEnabled())
                self.assertFalse(window.add_button.isEnabled())
                self.assertTrue(window.multi_button.isEnabled())
                self.assertEqual(
                    window.multi_button.text(),
                    "Отменить выбор",
                )
                self.assertIn(
                    "#F14C4C",
                    window.multi_button.styleSheet(),
                )

                item = next(
                    item
                    for item in window._iter_items()
                    if item.text(NAME_COLUMN) == "a.ogg"
                )
                item.setCheckState(SELECT_COLUMN, Qt.Checked)

                main_brush = item.background(NAME_COLUMN)
                self.assertEqual(main_brush.style(), Qt.SolidPattern)
                self.assertEqual(
                    main_brush.color().name().upper(),
                    "#2E436E",
                )

                pinned = window.pinned_list.topLevelItem(0)
                pinned_brush = pinned.background(NAME_COLUMN)
                self.assertEqual(pinned_brush.style(), Qt.SolidPattern)
                self.assertEqual(
                    pinned_brush.color().name().upper(),
                    "#2E436E",
                )

                item.setCheckState(SELECT_COLUMN, Qt.Unchecked)
                self.assertEqual(
                    item.background(NAME_COLUMN).style(),
                    Qt.NoBrush,
                )

                window.exit_selection_mode()
                self.assertTrue(window.refresh_button.isEnabled())
                self.assertTrue(window.section_button.isEnabled())
                self.assertTrue(window.add_button.isEnabled())
                self.assertEqual(
                    window.multi_button.text(),
                    "Мультиредактирование",
                )
                self.assertEqual(window.multi_button.styleSheet(), "")
                self.assertEqual(
                    item.background(NAME_COLUMN).style(),
                    Qt.NoBrush,
                )
            finally:
                window.close()

    def test_explorer_folder_selection_and_toggle(self):
        with tempfile.TemporaryDirectory() as d:
            game = Path(d) / "gamedata"
            ambient = game / "sounds" / "ambient" / "forest"
            ambient.mkdir(parents=True)
            comments = build_comment_packet(
                "enc",
                (build_xray_comment(XraySoundComment()),),
            )
            for path in (
                ambient / "wind.ogg",
                game / "sounds" / "ambient" / "rain.ogg",
                game / "sounds" / "click.ogg",
            ):
                path.write_bytes(_make_file(comments))

            ctx = _Ctx()
            window = OggExplorerWindow(
                ctx,
                on_open=lambda path: None,
                on_add=lambda: None,
            )
            try:
                window.set_game_root(game)
                self.assertEqual(
                    window.section_button.toolTip(),
                    "Ищет секцию в итоговой версии system.ltx",
                )
                window.enter_selection_mode()

                folder = next(
                    item
                    for item in window._iter_items()
                    if window._is_folder(item) and item.text(NAME_COLUMN) == "ambient"
                )
                folder.setCheckState(SELECT_COLUMN, Qt.Checked)
                self.assertEqual(len(window.selected_files()), 2)

                window._on_expanded(folder)
                forest = next(
                    item
                    for item in window._iter_items()
                    if window._is_folder(item) and item.text(NAME_COLUMN) == "forest"
                )
                window._on_expanded(forest)

                file_item = next(
                    item
                    for item in window._iter_items()
                    if item.text(NAME_COLUMN) == "wind.ogg"
                )
                window._on_item_clicked(file_item, NAME_COLUMN)
                self.assertEqual(len(window.selected_files()), 1)
                self.assertEqual(
                    folder.checkState(SELECT_COLUMN),
                    Qt.PartiallyChecked,
                )

                window._on_item_clicked(folder, NAME_COLUMN)
                self.assertEqual(len(window.selected_files()), 2)

                window._on_item_clicked(folder, NAME_COLUMN)
                self.assertEqual(len(window.selected_files()), 0)
            finally:
                window.close()

    def test_reader_returns_comment_view(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "a.ogg"
            comments = build_comment_packet(
                "enc",
                (
                    build_xray_comment(
                        XraySoundComment(min_dist=2.0, max_dist=30.0)
                    ),
                ),
            )
            path.write_bytes(_make_file(comments))

            reader = OggCommentReader()
            self.assertTrue(reader.can_read(path))
            self.assertFalse(reader.can_read(Path("a.txt")))

            view = reader.read(path, path.read_bytes())
            self.assertIn("min_dist = 2", view.text)
            self.assertIn("max_dist = 30", view.text)
            self.assertEqual(view.label, "Комментарии Ogg Vorbis")
            self.assertTrue(any("X-Ray v3" in item for item in view.details))

    def test_tree_and_filter_are_pure_data(self):
        root = Path("C:/game/gamedata/sounds")
        entries = [
            SoundEntry(root / "ambient" / "forest" / "wind.ogg"),
            SoundEntry(root / "ambient" / "rain.ogg"),
            SoundEntry(root / "click.ogg"),
        ]

        nodes = build_sound_tree(entries, root)
        self.assertEqual(
            [node.name for node in nodes],
            ["ambient", "click.ogg"],
        )

        filtered = filter_sound_tree(nodes, "wind")
        self.assertEqual([node.name for node in filtered], ["ambient"])
        forest = filtered[0].children[0]
        self.assertEqual(forest.name, "forest")
        self.assertEqual(forest.children[0].name, "wind.ogg")

        by_folder = filter_sound_tree(nodes, "ambient")
        self.assertEqual([node.name for node in by_folder], ["ambient"])
        self.assertEqual(len(by_folder[0].children), 2)

    def test_explorer_scan_and_filter_use_tasks(self):
        with tempfile.TemporaryDirectory() as d:
            game = Path(d) / "gamedata"
            sounds = game / "sounds"
            sounds.mkdir(parents=True)
            comments = build_comment_packet(
                "enc",
                (build_xray_comment(XraySoundComment()),),
            )
            (sounds / "a.ogg").write_bytes(_make_file(comments))

            ctx = _Ctx()
            window = OggExplorerWindow(
                ctx,
                on_open=lambda path: None,
                on_add=lambda: None,
            )
            try:
                window.set_game_root(game)
                self.assertEqual(ctx.tasks.calls, 1)

                window.filter_edit.setText("a.ogg")
                window._run_filter()
                self.assertEqual(ctx.tasks.calls, 2)
            finally:
                window.close()

    def test_explorer_groups_files_by_folder(self):
        with tempfile.TemporaryDirectory() as d:
            game = Path(d) / "gamedata"
            sounds = game / "sounds" / "ambient" / "forest"
            sounds.mkdir(parents=True)
            comments = build_comment_packet(
                "enc",
                (build_xray_comment(XraySoundComment()),),
            )
            (sounds / "wind.ogg").write_bytes(_make_file(comments))
            (game / "sounds" / "click.ogg").write_bytes(
                _make_file(comments)
            )

            ctx = _Ctx()
            window = OggExplorerWindow(
                ctx,
                on_open=lambda path: None,
                on_add=lambda: None,
            )
            try:
                window.set_game_root(game)
                self.assertEqual(window.tree.topLevelItemCount(), 2)
                names = {
                    window.tree.topLevelItem(index).text(NAME_COLUMN)
                    for index in range(window.tree.topLevelItemCount())
                }
                self.assertEqual(names, {"ambient", "click.ogg"})
            finally:
                window.close()


if __name__ == "__main__":
    unittest.main()
