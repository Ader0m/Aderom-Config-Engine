# -*- coding: utf-8 -*-
"""Модуль редактора комментариев .ogg.

Модуль зависит только от ``sdk`` и PyQt. Дерево VFS для звуков он
получает через ``ctx.services.game_root(forest)``; черновики и патч —
через проект платформы.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Dict, Optional

from PyQt5.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from sdk import (
    ModuleBase,
    ModuleManifest,
    ProjectAssetType,
    ProjectSetting,
    ProjectSettingChoice,
    path_key,
)
from sdk.events import Events

from .editor_tab import OggEditorTab
from .explorer import OggExplorerWindow, SoundOpenHandler
from .multi_edit import MultiEditDialog, common_values
from .ogg import (
    OggError,
    XraySoundComment,
    build_xray_comment,
    parse_xray_comment,
    read_comments,
    rewrite_engine_comment,
    validate_crc,
)
from .reader import OggCommentReader
from .section_links import OggSectionGraphContributor
from .sections import (
    extract_sound_paths,
    normalize_section_name,
    parse_section_fields,
    section_label,
)
from .sound_types_ui import configure_theme as configure_sound_type_theme


ASSET_TYPE = ProjectAssetType(
    key="sound.ogg",
    label="Звук Ogg Vorbis",
    extensions=(".ogg",),
    vfs_root="sounds",
)

OGG_SAVE_SETTING = ProjectSetting(
    key="ogg_editor.save_mode",
    label="ogg-патч",
    kind="choice",
    default="override",
    choices=(
        ProjectSettingChoice(
            value="override",
            label="Override — заменить файл целиком",
        ),
    ),
    description=(
        "Способ попадания изменённого .ogg в патч-мод. "
        "Сейчас поддерживается только полная замена файла."
    ),
)


def _is_ogg(path) -> bool:
    try:
        return Path(path).suffix.lower() in ASSET_TYPE.extensions
    except (TypeError, ValueError):
        return False


class OggEditorModule(ModuleBase):
    manifest = ModuleManifest(
        name="ogg_editor",
        version="0.1.0",
        sdk_min="0.1.0",
        description=(
            "Просмотр и правка Vorbis-комментариев .ogg (параметры звука "
            "X-Ray) с прослушиванием."
        ),
        requires=("base",),
    )

    WINDOW_KEY = "ogg_editor.window"
    WINDOW_COMMAND = "ogg_editor.window"
    OPEN_COMMAND = "ogg_editor.open"
    ADD_COMMAND = "ogg_editor.add"

    def init(self, ctx):
        super().init(ctx)
        try:
            configure_sound_type_theme(ctx.ui.theme_color)
        except Exception:
            pass
        self._subs = []
        self._window = None
        self._tabs: Dict[str, OggEditorTab] = {}
        self._asset_type_registered = False
        self._setting_registered = False
        self._file_reader_sub = None
        self._section_graph_contributor = None
        self._suspend_refresh = False

        self._subs.append(
            ctx.events.subscribe(Events.TREE_READY, self._on_tree_ready)
        )
        self._subs.append(
            ctx.events.subscribe(Events.PROJECT_SAVED, self._on_project_saved)
        )
        self._subs.append(
            ctx.events.subscribe(
                Events.PROJECT_CHANGED,
                self._on_project_changed,
            )
        )
        self._subs.append(
            ctx.events.subscribe(Events.UI_TAB_CLOSED, self._on_tab_closed)
        )

    def register(self, ctx):
        self._asset_type_registered = ctx.ui.register_project_asset_type(
            ASSET_TYPE
        )
        if self._asset_type_registered:
            ctx.ui.log(
                "ogg_editor: зарегистрирован тип ресурса %s (%s)"
                % (ASSET_TYPE.key, ASSET_TYPE.vfs_root)
            )
        else:
            ctx.ui.log(
                "ogg_editor: не удалось зарегистрировать тип %s"
                % ASSET_TYPE.key
            )

        self._setting_registered = ctx.ui.register_project_setting(
            OGG_SAVE_SETTING
        )
        if self._setting_registered:
            ctx.ui.log(
                "ogg_editor: добавлена настройка проекта %s"
                % OGG_SAVE_SETTING.key
            )
        else:
            ctx.ui.log(
                "ogg_editor: не удалось добавить настройку %s"
                % OGG_SAVE_SETTING.key
            )

        self._file_reader_sub = ctx.ui.register_file_reader(
            OggCommentReader()
        )

        self._section_graph_contributor = OggSectionGraphContributor(ctx)
        try:
            ctx.services.register_section_graph_contributor(
                self._section_graph_contributor
            )
        except Exception as exc:
            ctx.log(
                "ogg_editor: не удалось подключить связи секции: %s" % exc
            )
            self._section_graph_contributor = None

        ctx.ui.register_menu(
            "Инструменты",
            "Редактор звуков (.ogg)",
            self.show_window,
            "Ctrl+Alt+O",
            action="ogg_editor.window",
        )
        ctx.commands.register(self.WINDOW_COMMAND, self.show_window)
        ctx.commands.register(self.OPEN_COMMAND, self.open_file)
        ctx.commands.register(self.ADD_COMMAND, self.add_file)
        ctx.ui.register_open_handler(SoundOpenHandler(self))
        ctx.ui.register_tree_context_action(
            "Открыть в редакторе .ogg",
            self.open_file,
            predicate=_is_ogg,
        )

    def activate(self, ctx):
        ctx.log("Модуль редактора .ogg активирован.")

    def deactivate(self, ctx):
        if self._section_graph_contributor is not None:
            try:
                ctx.services.unregister_section_graph_contributor(
                    self._section_graph_contributor
                )
            except Exception:
                pass
            self._section_graph_contributor = None

        if self._file_reader_sub is not None:
            try:
                self._file_reader_sub.unsubscribe()
            except Exception:
                pass
            self._file_reader_sub = None

        if self._asset_type_registered:
            try:
                ctx.ui.unregister_project_asset_type(ASSET_TYPE.key)
            except Exception:
                pass
            self._asset_type_registered = False

        if self._setting_registered:
            try:
                ctx.ui.unregister_project_setting(OGG_SAVE_SETTING.key)
            except Exception:
                pass
            self._setting_registered = False

        for sub in self._subs:
            try:
                sub.unsubscribe()
            except Exception:
                pass
        self._subs.clear()

        for tab_id, widget in list(self._tabs.items()):
            try:
                widget.dispose()
            except Exception:
                pass
            try:
                ctx.ui.close_editor_tab(tab_id)
            except Exception:
                pass
        self._tabs.clear()

        if self._window is not None:
            try:
                ctx.ui.close_module_window(self.WINDOW_KEY)
            except Exception:
                pass
            self._window = None

    # ------------------------------------------------------------ окно
    def show_window(self):
        if self._window is None:
            self._window = OggExplorerWindow(
                self.ctx,
                on_open=self.open_file,
                on_add=self.add_file,
                on_multi_edit=self.multi_edit,
                on_edit_field=self.edit_file_params,
                on_open_section=self.open_section,
            )
        self._window.set_game_root(self._game_root())
        self.ctx.ui.show_module_window(self.WINDOW_KEY, self._window)

    def _game_root(self) -> Optional[Path]:
        forest = self.ctx.ui.workspace_forest()
        if forest is None:
            return None
        try:
            return self.ctx.services.game_root(forest)
        except Exception as exc:
            self.ctx.log("ogg_editor: не удалось определить gamedata: %s" % exc)
            return None

    def _on_tree_ready(self, _forest):
        if self._window is not None:
            self._window.set_game_root(self._game_root())

    # ------------------------------------------------------------ открытие
    def open_file(self, path):
        path = Path(path)
        if not _is_ogg(path):
            return

        tab_id = "ogg_editor." + hashlib.sha1(
            path_key(path).encode("utf-8", "replace")
        ).hexdigest()[:16]

        existing = self._tabs.get(tab_id)
        if existing is not None:
            self.ctx.ui.open_editor_tab(tab_id, path.name, existing)
            return

        widget = OggEditorTab(self.ctx, tab_id, path)
        self._tabs[tab_id] = widget
        self.ctx.ui.open_editor_tab(tab_id, path.name, widget)

    def _on_tab_closed(self, tab_id):
        widget = self._tabs.pop(str(tab_id), None)
        if widget is not None:
            try:
                widget.dispose()
            except Exception:
                pass

    # ------------------------------------------------------------ добавление
    def add_file(self):
        if not self.ctx.ui.is_project_active():
            self.ctx.ui.show_error(
                "Создайте или загрузите проект, чтобы добавлять .ogg."
            )
            return

        game_root = self._game_root()
        if game_root is None:
            self.ctx.ui.show_error(
                "Сначала выполните «Пересобрать VFS», чтобы определить gamedata."
            )
            return

        source, _filter = QFileDialog.getOpenFileName(
            self._window,
            "Добавить .ogg в проект",
            "",
            "Ogg Vorbis (*.ogg);;Все файлы (*.*)",
        )
        if not source:
            return

        try:
            data = Path(source).read_bytes()
            validate_crc(data)
            read_comments(data)
        except (OSError, OggError) as exc:
            self.ctx.ui.show_error("Не удалось прочитать .ogg: %s" % exc)
            return

        default = ASSET_TYPE.vfs_root + "/" + Path(source).name
        relative_text, ok = QInputDialog.getText(
            self._window,
            "Путь в VFS",
            "Путь относительно gamedata (например, %s/…):"
            % ASSET_TYPE.vfs_root,
            text=default,
        )
        if not ok:
            return

        relative_text = str(relative_text or "").strip().replace("\\", "/")
        if not relative_text:
            return

        relative = Path(relative_text)
        if relative.is_absolute() or ".." in relative.parts:
            self.ctx.ui.show_error("Путь должен быть относительным и без '..'.")
            return
        if (
            not relative.parts
            or relative.parts[0].lower() != ASSET_TYPE.vfs_root
        ):
            self.ctx.ui.show_error(
                "Звуки проекта должны лежать в gamedata/%s."
                % ASSET_TYPE.vfs_root
            )
            return
        if relative.suffix.lower() not in ASSET_TYPE.extensions:
            self.ctx.ui.show_error(
                "Файл должен иметь расширение %s."
                % ", ".join(ASSET_TYPE.extensions)
            )
            return

        target = Path(game_root) / relative
        try:
            target.relative_to(game_root)
        except ValueError:
            self.ctx.ui.show_error("Путь выходит за пределы gamedata.")
            return

        if target.is_file():
            answer = QMessageBox.question(
                self._window,
                "Файл уже существует",
                "Файл уже есть в VFS. Добавить его изменённую версию "
                "в проект?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        if not self.ctx.ui.update_project_asset(target, data):
            return
        # Импорт — уже готовое изменение: сразу кладём его в каталог
        # проекта, чтобы новый звук не потерялся до автосохранения.
        self.ctx.ui.save_project_asset(target, data)

        if self._window is not None:
            self._window.reload()
        self.open_file(target)

    # ------------------------------------------------------------ секции
    def open_section(self):
        forest = self.ctx.ui.workspace_forest()
        if forest is None:
            self.ctx.ui.show_error(
                "Сначала выполните «Пересобрать VFS», чтобы построить VFS."
            )
            return

        raw_section, ok = QInputDialog.getText(
            self._window,
            "Открыть секцию",
            "Имя секции (например, wpn_ak74 или [wpn_ak74]):",
        )
        section = normalize_section_name(raw_section)
        if not ok or not section:
            return
        label = section_label(section)

        config_dir = self.ctx.services.forest_scan_dir(forest)
        game_root = self.ctx.services.game_root(forest)
        overlay = self.ctx.ui.project_overlay()

        self.ctx.ui.set_status("Поиск секции %s…" % label, 5000)

        def work(log):
            graph = self.ctx.services.build_section_graph(
                forest,
                section,
                overlay=overlay,
            )
            root_path = Path(graph.root.file_path)
            if root_path.suffix.lower() == ".xml":
                raise RuntimeError(
                    "Секция найдена в XML; для звуковых полей нужен LTX."
                )
            if config_dir is None:
                raise RuntimeError("Не определён каталог configs.")

            # Берём именно resolved-текст (итоговый System.ltx), чтобы
            # учитывать include, наследование и DLTX-правки.
            text, _line_sources = self.ctx.services.assemble_resolved(
                root_path,
                forest,
                config_dir,
                overlay=overlay,
            )
            fields = parse_section_fields(text, section)
            sounds_root = (
                Path(game_root) / "sounds"
                if game_root is not None
                else None
            )
            paths = extract_sound_paths(fields, sounds_root)
            return label, fields, paths

        def done(result, error):
            if error is not None:
                self.ctx.ui.show_error(
                    "Не удалось открыть секцию: %s" % error
                )
                return

            section_name, fields, paths = result
            if not fields:
                self.ctx.ui.set_status(
                    "Секция %s не найдена или пуста" % section_name,
                    5000,
                )
                return

            if not paths:
                self.ctx.ui.set_status(
                    "В секции %s полей: %d, но .ogg не найдены"
                    % (section_name, len(fields)),
                    6000,
                )
                return

            if self._window is not None:
                added = self._window.pin_paths(paths)
                self._window.splitter.setSizes([400, 240])
                self.ctx.ui.set_status(
                    "Секция %s: закреплено файлов %d"
                    % (section_name, added),
                    6000,
                )

        self.ctx.tasks.run(
            work,
            on_done=done,
            on_log=self.ctx.log,
        )

    # ------------------------------------------------------------ мультиредактирование
    def _read_current_bytes(self, path):
        data = self.ctx.ui.read_project_asset(path)
        if data is None:
            data = Path(path).read_bytes()
        return bytes(data)

    @staticmethod
    def _merge_multi_values(base, patch):
        base = base or XraySoundComment()
        version = (
            int(base.version)
            if patch.version is None
            else int(patch.version)
        )

        if patch.version is None:
            if patch.base_volume is not None:
                version = max(version, 2)
            if patch.max_ai_dist is not None:
                version = max(version, 3)

        return XraySoundComment(
            version=version,
            min_dist=(
                base.min_dist
                if patch.min_dist is None
                else patch.min_dist
            ),
            max_dist=(
                base.max_dist
                if patch.max_dist is None
                else patch.max_dist
            ),
            base_volume=(
                base.base_volume
                if patch.base_volume is None
                else patch.base_volume
            ),
            game_type=(
                base.game_type
                if patch.game_type is None
                else patch.game_type
            ),
            max_ai_dist=(
                base.max_ai_dist
                if patch.max_ai_dist is None
                else patch.max_ai_dist
            ),
        )

    def _refresh_open_tab(self, path, data):
        wanted = path_key(path)
        for widget in self._tabs.values():
            current = path_key(widget.path)
            if current != wanted:
                continue
            try:
                widget.apply_external_data(data)
            except Exception as exc:
                self.ctx.log(
                    "ogg_editor: не удалось обновить вкладку %s: %s"
                    % (path.name, exc)
                )

    def edit_file_params(self, path, params):
        if not self.ctx.ui.is_project_active():
            return False, "Создайте или загрузите проект."

        try:
            data = self._read_current_bytes(path)
            validate_crc(data)
            rewritten = rewrite_engine_comment(
                data,
                build_xray_comment(params),
            )
        except Exception as exc:
            return False, str(exc)

        self._suspend_refresh = True
        try:
            if not self.ctx.ui.update_project_asset(path, rewritten):
                return False, "Не удалось обновить черновик проекта."
            if not self.ctx.ui.save_project_asset(path, rewritten):
                return False, "Не удалось сохранить черновик проекта."
        finally:
            self._suspend_refresh = False

        self._refresh_open_tab(path, rewritten)
        self.ctx.ui.set_status(
            "Параметры сохранены в проекте: %s" % Path(path).name,
            5000,
        )
        return True, ""

    def multi_edit(self, paths):
        if not self.ctx.ui.is_project_active():
            self.ctx.ui.show_error(
                "Создайте или загрузите проект, чтобы изменять звуки."
            )
            return

        entries = []
        errors = []
        params_list = []

        for path in [Path(item) for item in paths]:
            try:
                data = self._read_current_bytes(path)
                validate_crc(data)
                comments = read_comments(data)
                params = parse_xray_comment(comments.engine_comment)
            except Exception as exc:
                errors.append("%s: %s" % (path.name, exc))
                continue
            entries.append((path, data, params))
            params_list.append(params)

        if not entries:
            self.ctx.ui.show_error(
                "Нечего редактировать:\n%s" % "\n".join(errors[:20])
            )
            return

        dialog = MultiEditDialog(
            self._window,
            count=len(entries),
            initial=common_values(params_list),
            theme_color=self.ctx.ui.theme_color,
        )
        if not dialog.exec_():
            return

        patch = dialog.values()
        if not patch.has_any():
            self.ctx.ui.show_error(
                "Не задано ни одного значения для мультиредактирования."
            )
            return

        def work(log):
            results = []
            for path, data, base in entries:
                try:
                    params = self._merge_multi_values(base, patch)
                    comment = build_xray_comment(params)
                    rewritten = rewrite_engine_comment(data, comment)
                except Exception as exc:
                    results.append((path, None, str(exc)))
                    log("ogg_editor: %s: %s" % (path.name, exc))
                else:
                    results.append((path, rewritten, ""))
            return results

        def done(results, error):
            if error is not None:
                self.ctx.ui.show_error(
                    "Мультиредактирование не выполнено: %s" % error
                )
                return

            failed = list(errors)
            applied = 0

            self._suspend_refresh = True
            try:
                for path, rewritten, reason in results:
                    if rewritten is None:
                        failed.append("%s: %s" % (path.name, reason))
                        continue
                    if self.ctx.ui.update_project_asset(path, rewritten):
                        applied += 1
                    else:
                        failed.append(
                            "%s: не удалось обновить черновик" % path.name
                        )

                if applied:
                    self.ctx.ui.save_project_now()
            finally:
                self._suspend_refresh = False

            if self._window is not None:
                self._window.exit_selection_mode()
                self._window.reload()

            self.ctx.ui.set_status(
                "Мультиредактирование: изменено файлов %d" % applied,
                7000,
            )
            self.ctx.log(
                "ogg_editor: мультиредактирование применило %d файл(ов)"
                % applied
            )

            if failed:
                QMessageBox.warning(
                    self._window,
                    "Мультиредактирование",
                    "Обработаны не все файлы:\n%s"
                    % "\n".join(failed[:20]),
                )

        self.ctx.tasks.run(
            work,
            on_done=done,
            on_log=self.ctx.log,
        )

    # ------------------------------------------------------------ события
    def _on_project_saved(self, event):
        if getattr(event, "kind", "") != "asset":
            return
        if self._suspend_refresh:
            return
        if self._window is not None:
            self._window.reload()

    def _on_project_changed(self, _project):
        if self._window is not None:
            self._window.reload()


MODULE = OggEditorModule()
